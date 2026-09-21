#include "dnp3host/HostController.h"
#include "dnp3host/ProtocolTrace.h"
#include "dnp3host/TraceConfig.h"

#include <algorithm>
#include <chrono>
#include <exception>
#include <iostream>
#include <string_view>
#include <thread>

namespace {

int failures = 0;

void check(const bool condition, const std::string_view message)
{
    if (!condition) {
        ++failures;
        std::cerr << "FAILED: " << message << '\n';
    }
}

void test_configuration()
{
    using dnp3host::Json;
    dnp3host::TraceStartConfig start;
    check(!dnp3host::parse_trace_start_config(Json::object(), start), "start defaults valid");
    check(start.queue_capacity == 16384, "default trace capacity");
    for (const auto& value : {Json(true), Json(-1), Json(0), Json(65537), Json(1.0),
                              Json("1"), Json(nullptr), Json(18446744073709551615ULL)}) {
        check(dnp3host::parse_trace_start_config(Json{{"queue_capacity", value}}, start)
                  .has_value(), "invalid capacities rejected");
    }
    check(dnp3host::parse_trace_start_config(Json{{"unknown", 1}}, start).has_value(),
          "unknown start key rejected");
    check(dnp3host::parse_trace_start_config(Json::array(), start).has_value(),
          "nonobject start rejected");
    for (const auto capacity : {1, 65536}) {
        check(!dnp3host::parse_trace_start_config(Json{{"queue_capacity", capacity}}, start),
              "capacity boundaries accepted");
    }
    dnp3host::TraceReferenceConfig reference;
    check(!dnp3host::parse_trace_reference_config(Json{{"trace_id", "trace-1"}}, true, reference),
          "read defaults valid");
    check(reference.max_records == 256 && reference.timeout_ms == 0, "read defaults");
    for (const auto& id : {Json(""), Json("trace-0"), Json("trace-01"), Json("trace-1x"),
                           Json("trace-"), Json("x"), Json(true), Json(2),
                           Json("trace-" + std::string(59, '1'))}) {
        check(dnp3host::parse_trace_reference_config(Json{{"trace_id", id}}, true, reference)
                  .has_value(), "invalid trace IDs rejected");
    }
    check(dnp3host::parse_trace_reference_config(Json::object(), true, reference).has_value(),
          "missing trace ID rejected");
    check(dnp3host::parse_trace_reference_config(Json::array(), true, reference).has_value(),
          "nonobject reference rejected");
    for (const auto& extra : {Json{{"max_records", 0}}, Json{{"max_records", 1025}},
                              Json{{"max_records", true}}, Json{{"timeout_ms", -1}},
                              Json{{"timeout_ms", 60001}}, Json{{"timeout_ms", 1.5}},
                              Json{{"unknown", 1}}}) {
        auto input = extra;
        input["trace_id"] = "trace-1";
        check(dnp3host::parse_trace_reference_config(input, true, reference).has_value(),
              "invalid read options rejected");
    }
    check(!dnp3host::parse_trace_reference_config(
              Json{{"trace_id", "trace-1"}, {"max_records", 1024}, {"timeout_ms", 60000}},
              true, reference), "read boundaries accepted");
    check(dnp3host::parse_trace_reference_config(
              Json{{"trace_id", "trace-1"}, {"timeout_ms", 0}}, false, reference).has_value(),
          "stop rejects read options");
}

void test_queue_lifecycle()
{
    dnp3host::ProtocolTrace trace;
    check(trace.status().at("state") == "IDLE", "trace defaults off");
    trace.record(1, "test", 1, "ignored before start");
    check(trace.status().at("last_sequence") == 0, "disabled trace stores nothing");
    check(trace.read({"trace-1"}).error.has_value(), "read before start rejected");
    check(trace.stop({"trace-1"}).error.has_value(), "stop before start rejected");
    check(trace.start({0}).error.has_value(), "direct invalid start rejected");
    const auto started = trace.start({2});
    check(!started.error && started.result.at("trace_id") == "trace-1", "first trace starts");
    check(trace.start({2}).error.has_value(), "double start rejected");
    trace.record(0, "test", 1, "zero session ignored");
    trace.record(1, "test", 1 << 8, "a");
    trace.record(1, "test", 1 << 6, "b");
    trace.record(2, "test", 1 << 11, "c");
    check(trace.read({"trace-2"}).error.has_value(), "wrong ID does not consume");
    check(trace.read({"trace-1", 0, 0}).error.has_value(), "invalid direct read rejected");
    const auto first = trace.read({"trace-1", 1, 0});
    check(!first.error, "first read succeeds");
    check(first.result.at("records").size() == 1, "read is bounded");
    check(first.result.at("records").at(0).at("sequence") == 2, "drop oldest preserves sequence gap");
    check(first.result.at("records").at(0).at("level") == "LINK_RX_HEX", "level is symbolic");
    check(first.result.at("dropped_records") == 1 && first.result.at("complete") == false,
          "drop makes trace explicitly incomplete");
    check(first.result.at("queued_records") == 1, "remaining queue count exact");
    check(trace.stop({"trace-2"}).error.has_value(), "wrong stop ID rejected");
    check(!trace.stop({"trace-1"}).error, "trace stops");
    check(!trace.stop({"trace-1"}).error, "stop idempotent");
    trace.record(2, "test", 1, "ignored after stop");
    check(trace.start({2}).error.has_value(), "unread stopped trace cannot be overwritten");
    const auto last = trace.read({"trace-1", 10, 60000});
    check(last.result.at("records").size() == 1, "stopped queue remains readable");
    check(last.result.at("records").at(0).at("session_id") == 2, "session immutable per log record");
    const auto empty = trace.read({"trace-1", 10, 60000});
    check(empty.result.at("timed_out") == true, "empty stopped trace returns immediately");
    const auto restarted = trace.start({8});
    check(!restarted.error && restarted.result.at("trace_id") == "trace-2", "new trace gets unique ID");
    check(restarted.result.at("complete") == true && restarted.result.at("last_sequence") == 0,
          "new trace resets counters");
    trace.record(3, "test", -1, "other level");
    trace.shutdown();
    check(trace.status().at("state") == "STOPPED", "shutdown stops collection");
    const auto after_shutdown = trace.read({"trace-2"});
    check(after_shutdown.result.at("records").at(0).at("level") == "OTHER", "unknown log flags explicit");
}

void test_utf8_and_levels()
{
    dnp3host::ProtocolTrace trace;
    trace.start({32});
    for (std::size_t level = 0; level < 17; ++level) {
        trace.record(1, "logger", static_cast<std::int32_t>(1U << level), "details");
    }
    trace.record(1, "logger", 1, std::string(1024, 'a').c_str());
    trace.record(1, "logger", 1, std::string(1025, 'a').c_str());
    const auto boundary = std::string(1023, 'a') + "\xE4\xB8\xAD";
    trace.record(1, "logger", 1, boundary.c_str());
    const auto exact_utf8 = std::string(1021, 'a') + "\xE4\xB8\xAD";
    trace.record(1, "logger", 1, exact_utf8.c_str());
    trace.record(1, "logger", 1, "bad \xFF \xED\xA0\x80 \xE4\xB8");
    trace.record(1, std::string(129, 'x').c_str(), 1, "long logger");
    const auto result = trace.read({"trace-1", 32, 0}).result;
    const auto& records = result.at("records");
    check(result.at("truncated_records") == 4, "truncation and invalid UTF8 explicitly counted");
    check(result.at("complete") == false, "truncated trace is incomplete");
    check(records.at(17).at("message_truncated") == false, "exact byte boundary preserved");
    check(records.at(18).at("message").get<std::string>().size() == 1024,
          "oversized message bounded");
    check(records.at(19).at("message").get<std::string>().size() == 1023,
          "UTF8 character never split");
    check(records.at(20).at("message_truncated") == false, "valid UTF8 boundary retained");
    check(records.at(22).at("logger").get<std::string>().size() == 128, "logger is bounded");
    for (std::size_t index = 0; index < 17; ++index) {
        check(records.at(index).at("level") != "OTHER", "all public log levels mapped");
    }
    // nlohmann's strict serializer rejects any remaining malformed UTF-8.
    check(!result.dump().empty(), "all serialized log strings are valid UTF8");
}

void test_wait_and_parallel_record()
{
    dnp3host::ProtocolTrace trace;
    trace.start({256});
    std::thread producer([&trace] {
        std::this_thread::sleep_for(std::chrono::milliseconds{20});
        trace.record(1, "thread", 1, "wake");
    });
    const auto read = trace.read({"trace-1", 256, 1000});
    producer.join();
    check(read.result.at("timed_out") == false, "read wakes on new log");
    const auto timeout = trace.read({"trace-1", 256, 5});
    check(timeout.result.at("timed_out") == true, "bounded read timeout succeeds empty");
    std::thread first([&trace] {
        for (int index = 0; index < 100; ++index) {
            trace.record(1, "first", 1, "details");
        }
    });
    std::thread second([&trace] {
        for (int index = 0; index < 100; ++index) {
            trace.record(2, "second", 1, "details");
        }
    });
    first.join();
    second.join();
    const auto all = trace.read({"trace-1", 256, 0}).result;
    check(all.at("records").size() == 200 && all.at("complete") == true,
          "concurrent producers preserve every record");
    std::uint64_t sequence = 1;
    for (const auto& record : all.at("records")) {
        check(record.at("sequence") == ++sequence, "records strictly ordered");
    }
    std::thread shutdown([&trace] {
        std::this_thread::sleep_for(std::chrono::milliseconds{20});
        trace.shutdown();
    });
    check(trace.read({"trace-1", 256, 1000}).result.at("state") == "STOPPED",
          "waiting reader wakes on shutdown");
    shutdown.join();
}

void test_controller_dispatch()
{
    using dnp3host::Json;
    dnp3host::HostController controller(1048576);
    const auto hello = controller.dispatch({"t-hello", "hello", Json::object()}).response;
    const auto& commands = hello.at("result").at("supported_commands");
    for (const auto* command : {"trace.start", "trace.read", "trace.stop"}) {
        check(std::find(commands.begin(), commands.end(), Json(command)) != commands.end(),
              "hello advertises trace commands");
    }
    const auto started = controller.dispatch({"t-start", "trace.start", Json{{"queue_capacity", 2}}});
    check(started.response.at("ok") == true, "controller dispatches start");
    const auto invalid = controller.dispatch({"t-invalid", "trace.read", Json{{"trace_id", "trace-1"}, {"max_records", true}}});
    check(invalid.response.at("error").at("code") == "INVALID_REQUEST", "controller rejects invalid read");
    const auto read = controller.dispatch({"t-read", "trace.read", Json{{"trace_id", "trace-1"}}});
    check(read.response.at("ok") == true && read.response.at("result").at("timed_out") == true,
          "controller dispatches read");
    const auto stopped = controller.dispatch({"t-stop", "trace.stop", Json{{"trace_id", "trace-1"}}});
    check(stopped.response.at("result").at("state") == "STOPPED", "controller dispatches stop");
    for (const auto* command : {"get_status", "stats"}) {
        const auto response = controller.dispatch({std::string("t-") + command, command, Json::object()});
        check(response.response.at("result").at("trace").at("state") == "STOPPED",
              "status and stats include trace summary");
    }
}

}  // namespace

int main()
{
    try {
        test_configuration();
        test_queue_lifecycle();
        test_utf8_and_levels();
        test_wait_and_parallel_record();
        test_controller_dispatch();
    }
    catch (const std::exception& error) {
        std::cerr << "UNEXPECTED EXCEPTION: " << error.what() << '\n';
        return 1;
    }
    return failures == 0 ? 0 : 1;
}
