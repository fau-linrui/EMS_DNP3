#include "dnp3host/CaptureConfig.h"
#include "dnp3host/MeasurementCapture.h"

#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

namespace {

int failures = 0;

void check(const bool condition, const std::string_view message)
{
    if (!condition) {
        ++failures;
        std::cerr << "FAILED: " << message << '\n';
    }
}

dnp3host::CaptureConfig static_config(
    const std::size_t queue_capacity = 32,
    const std::uint32_t duration_limit_ms = 5000)
{
    dnp3host::CaptureConfig config;
    config.mode = dnp3host::CaptureMode::StaticSet;
    config.sources = {"solicited"};
    config.point_ranges = {{"analog_input", 0, 1}};
    config.mismatch_sample_limit = 2;
    config.queue_capacity = queue_capacity;
    config.duration_limit_ms = duration_limit_ms;
    return config;
}

void test_strict_configuration_parser()
{
    dnp3host::CaptureConfig config;
    const auto valid = dnp3host::parse_capture_config(
        dnp3host::Json{
            {"mode", "static_set"},
            {"sources", dnp3host::Json::array({"solicited", "unsolicited"})},
            {"duration_limit_ms", 1000},
            {"queue_capacity", 17},
            {"mismatch_sample_limit", 3},
            {"expected",
             dnp3host::Json{{"point_ranges",
                             dnp3host::Json::array(
                                 {dnp3host::Json{{"kind", "analog_input"},
                                                 {"start", 10},
                                                 {"stop", 19}},
                                  dnp3host::Json{{"kind", "binary_input"},
                                                 {"start", 0},
                                                 {"stop", 1}}})}}}},
        config);
    check(!valid.has_value(), "valid static capture configuration must parse");
    check(config.point_ranges.size() == 2, "point ranges must be retained");
    check(config.queue_capacity == 17, "queue capacity must be retained");

    const auto overlap = dnp3host::parse_capture_config(
        dnp3host::Json{
            {"mode", "static_set"},
            {"sources", dnp3host::Json::array({"solicited"})},
            {"duration_limit_ms", 1000},
            {"expected",
             dnp3host::Json{{"point_ranges",
                             dnp3host::Json::array(
                                 {dnp3host::Json{{"kind", "analog_input"},
                                                 {"start", 0},
                                                 {"stop", 10}},
                                  dnp3host::Json{{"kind", "analog_input"},
                                                 {"start", 10},
                                                 {"stop", 20}}})}}}},
        config);
    check(overlap.has_value(), "overlapping point ranges must be rejected");
    check(
        overlap && overlap->details.at("reason") == "overlapping_ranges",
        "overlap rejection reason must be stable");

    const auto observation_truth = dnp3host::parse_capture_config(
        dnp3host::Json{
            {"mode", "observation"},
            {"sources", dnp3host::Json::array({"unsolicited"})},
            {"duration_limit_ms", 1000},
            {"expected", dnp3host::Json::object()}},
        config);
    check(
        observation_truth.has_value(),
        "observation capture must reject a fake expected set");

    const auto event = dnp3host::parse_capture_config(
        dnp3host::Json{
            {"mode", "event_sequence"},
            {"sources", dnp3host::Json::array({"unsolicited"})},
            {"duration_limit_ms", 1000},
            {"expected",
             dnp3host::Json{{"manifest",
                             dnp3host::Json{
                                 {"generator", "local-outstation"},
                                 {"generator_version", "1"},
                                 {"scenario_id", "burst-1"},
                                 {"seed", 7},
                                 {"start_sequence", 10},
                                 {"end_sequence", 11},
                                 {"event_total", 2},
                                 {"sha256", std::string(64, 'a')},
                                 {"match_rule", "ordered_kind_index_value"}}}}}},
        config);
    check(!event.has_value(), "valid event manifest must parse");
    check(config.event_manifest.has_value(), "event manifest must be retained");

    dnp3host::CaptureReferenceConfig reference;
    const auto progress = dnp3host::parse_capture_reference_config(
        dnp3host::Json{{"capture_id", "cap-1-1"}, {"drain_timeout_ms", 10}},
        false,
        reference);
    check(progress.has_value(), "progress must reject end-only drain timeout");
}

void test_static_set_state_machine()
{
    dnp3host::MeasurementCapture capture;
    const auto began = capture.begin(7, static_config());
    check(!began.error.has_value(), "capture begin must succeed");
    const auto capture_id = began.result.at("capture_id").get<std::string>();
    check(began.result.at("state") == "ACTIVE", "new capture must be active");

    capture.record_fragment("solicited", 100);
    capture.record_object(
        "solicited", "analog_input", 30, 5, std::uint16_t{0}, 1.0, 101);
    capture.record_object(
        "solicited", "analog_input", 30, 5, std::uint16_t{1}, 2.0, 102);
    capture.record_object(
        "solicited", "analog_input", 30, 5, std::uint16_t{1}, 2.0, 103);
    capture.record_object(
        "solicited", "binary_input", 1, 2, std::uint16_t{0}, false, 104);
    capture.record_object(
        "unsolicited", "analog_input", 32, 7, std::uint16_t{0}, 3.0, 105);

    dnp3host::CaptureReferenceConfig reference;
    reference.capture_id = capture_id;
    reference.drain_timeout_ms = 1000;
    const auto ended = capture.end(reference);
    check(!ended.error.has_value(), "bounded capture end must succeed");
    check(ended.result.at("state") == "FINALIZED", "capture must finalize");
    check(
        ended.result.at("valid") == false,
        "duplicates or unexpected points must invalidate static truth");
    check(ended.result.at("expected_total") == 2, "expected total must be exact");
    check(ended.result.at("offered_total") == 4, "source filter must exclude unsolicited data");
    check(ended.result.at("received_total") == 4, "all queued objects must drain");
    check(ended.result.at("received_unique") == 2, "unique expected points must be counted");
    check(ended.result.at("duplicates") == 1, "duplicate static points must be counted");
    check(ended.result.at("missing") == 0, "complete expected set must have no missing points");
    check(ended.result.at("unmatched_total") == 1, "unexpected points must be counted");
    check(
        ended.result.at("invalid_reasons").at(0) == "STATIC_SET_MISMATCH",
        "static truth mismatch reason must be stable");
    check(ended.result.at("mismatch_sample").size() == 1, "mismatch samples must be bounded");

    const auto repeated = capture.end(reference);
    check(!repeated.error.has_value(), "repeated capture.end must be idempotent");
    check(repeated.result == ended.result, "repeated capture.end must return the same terminal summary");

    reference.capture_id = "cap-other";
    const auto wrong = capture.progress(reference);
    check(
        wrong.error && wrong.error->code == dnp3host::ErrorCode::InvalidState,
        "wrong capture id must fail closed");
}

void test_deadline_abort_and_overflow()
{
    dnp3host::MeasurementCapture timed;
    const auto began = timed.begin(1, static_config(8, 100));
    dnp3host::CaptureReferenceConfig reference;
    reference.capture_id = began.result.at("capture_id").get<std::string>();
    std::this_thread::sleep_for(std::chrono::milliseconds{140});
    const auto progress = timed.progress(reference);
    check(!progress.error.has_value(), "deadline alone must not masquerade as queue overflow");
    check(progress.result.at("state") == "TIMED_OUT", "deadline must produce TIMED_OUT");
    check(progress.result.at("valid") == false, "timed-out capture must be invalid");

    dnp3host::MeasurementCapture aborted;
    const auto abort_begin = aborted.begin(2, static_config());
    const auto abort_result = aborted.abort("CAPTURE_SESSION_DISCONNECTED");
    check(abort_result.at("state") == "ABORTED", "disconnect must abort active capture");
    check(abort_result.at("valid") == false, "aborted capture must be invalid");
    check(
        abort_result.at("invalid_reasons").at(0) == "CAPTURE_SESSION_DISCONNECTED",
        "abort reason must be retained");
    check(!abort_begin.error.has_value(), "abort test capture must begin");

    dnp3host::MeasurementCapture overflowing;
    const auto overflow_begin = overflowing.begin(3, static_config(1, 5000));
    const auto overflow_id = overflow_begin.result.at("capture_id").get<std::string>();
    std::vector<std::thread> producers;
    for (int thread_index = 0; thread_index < 4; ++thread_index) {
        producers.emplace_back([&overflowing, thread_index] {
            for (int item = 0; item < 5000; ++item) {
                overflowing.record_object(
                    "solicited",
                    "analog_input",
                    30,
                    5,
                    static_cast<std::uint16_t>((item + thread_index) % 2),
                    static_cast<double>(item),
                    static_cast<std::uint64_t>(item + 1));
            }
        });
    }
    for (auto& producer : producers) {
        producer.join();
    }
    reference.capture_id = overflow_id;
    const auto overflow_end = overflowing.end(reference);
    check(
        overflow_end.error
            && overflow_end.error->code == dnp3host::ErrorCode::QueueOverflow,
        "bounded queue overflow must return QUEUE_OVERFLOW");
    if (overflow_end.error) {
        const auto& terminal = overflow_end.error->details.at("operation_result");
        check(terminal.at("state") == "FINALIZED", "overflow must retain a terminal result");
        check(terminal.at("valid") == false, "overflow terminal result must be invalid");
        check(terminal.at("queue_overflow").get<std::uint64_t>() > 0, "overflow count must be non-zero");
    }
}

void test_event_sequence_digest_truth()
{
    dnp3host::CaptureConfig config;
    config.mode = dnp3host::CaptureMode::EventSequence;
    config.sources = {"unsolicited"};
    config.duration_limit_ms = 5000;
    config.queue_capacity = 16;
    config.mismatch_sample_limit = 2;
    config.event_manifest = dnp3host::CaptureEventManifest{
        "local_deterministic_event_generator",
        "1",
        "digest-match",
        7,
        10,
        11,
        2,
        "15342e8556e4e911329df66f1973d22ef0fcafe15962fbacf1f07435fcf22dc6",
        "ordered_kind_index_value"};

    dnp3host::MeasurementCapture matching;
    const auto began = matching.begin(9, config);
    dnp3host::CaptureReferenceConfig reference;
    reference.capture_id = began.result.at("capture_id").get<std::string>();
    reference.drain_timeout_ms = 1000;
    matching.record_object(
        "unsolicited", "analog_input", 32, 7, std::uint16_t{0}, 10.0, 100);
    matching.record_object(
        "unsolicited", "analog_input", 32, 7, std::uint16_t{1}, 11.0, 101);
    const auto ended = matching.end(reference);
    check(!ended.error.has_value(), "matching event digest must finalize");
    check(ended.result.at("valid") == true, "matching event digest must be valid");
    check(
        ended.result.at("sequence_match") == true,
        "matching event sequence must report sequence_match");
    check(
        ended.result.at("received_sequence_sha256")
            == config.event_manifest->sha256,
        "received event digest must be auditable");
    check(
        ended.result.at("unknown_reason").is_null(),
        "matched external truth must not remain unknown");

    config.event_manifest->scenario_id = "digest-mismatch";
    dnp3host::MeasurementCapture mismatching;
    const auto mismatch_begin = mismatching.begin(10, config);
    reference.capture_id =
        mismatch_begin.result.at("capture_id").get<std::string>();
    mismatching.record_object(
        "unsolicited", "analog_input", 32, 7, std::uint16_t{1}, 10.0, 100);
    mismatching.record_object(
        "unsolicited", "analog_input", 32, 7, std::uint16_t{0}, 11.0, 101);
    const auto mismatch_end = mismatching.end(reference);
    check(!mismatch_end.error.has_value(), "truth mismatch is a completed capture result");
    check(
        mismatch_end.result.at("valid") == false,
        "event order mismatch must invalidate the capture");
    check(
        mismatch_end.result.at("sequence_match") == false,
        "event order mismatch must be explicit");
    check(
        mismatch_end.result.at("invalid_reasons").at(0)
            == "EVENT_SEQUENCE_MISMATCH",
        "event mismatch reason must be stable");
}

}  // namespace

int main()
{
    test_strict_configuration_parser();
    test_static_set_state_machine();
    test_deadline_abort_and_overflow();
    test_event_sequence_digest_truth();
    if (failures != 0) {
        std::cerr << failures << " capture test(s) failed\n";
        return 1;
    }
    std::cout << "measurement capture tests passed\n";
    return 0;
}
