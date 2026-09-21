#include "dnp3host/Backend.h"
#include "dnp3host/CaptureConfig.h"
#include "dnp3host/CommandConfig.h"
#include "dnp3host/ConnectionConfig.h"
#include "dnp3host/HostController.h"
#include "dnp3host/JsonLineProtocol.h"
#include "dnp3host/Models.h"
#include "dnp3host/ReadConfig.h"
#include "dnp3host/ProtocolTrace.h"

#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

int failures = 0;

class FakeBackend final : public dnp3host::IMasterBackend {
public:
    std::string name() const override
    {
        return "fake";
    }

    std::string version() const override
    {
        return "test-1";
    }

    std::vector<std::string> supported_commands() const override
    {
        return {
            "capture.begin",
            "capture.end",
            "capture.progress",
            "class_poll",
            "connect",
            "direct_operate",
            "disable_unsolicited",
            "disconnect",
            "enable_unsolicited",
            "get_status",
            "hello",
            "integrity_poll",
            "read",
            "select_and_operate",
            "shutdown",
            "stats",
            "wait_event",
            "wait_unsolicited"};
    }

    dnp3host::Json capabilities() const override
    {
        return dnp3host::Json{
            {"CHANNEL.TCP.CLIENT",
             dnp3host::Json{{"status", "IMPLEMENTED_UNVERIFIED"},
                            {"implementation_revision", "fake-test"}}}};
    }

    dnp3host::BackendStatus status() const override
    {
        return dnp3host::BackendStatus{
            connected, connected ? "OPEN" : "SHUTDOWN", session_id, 4, 0, 0};
    }

    dnp3host::BackendOperationResult connect(
        const dnp3host::ConnectionConfig& config) override
    {
        ++connect_calls;
        last_config = config;
        if (connected) {
            return dnp3host::BackendOperationResult::failure(
                dnp3host::ErrorCode::AlreadyConnected,
                "already connected",
                dnp3host::Json{{"session_id", session_id}});
        }
        connected = true;
        ++session_id;
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"state", "CONNECTED"},
            {"channel_state", "OPEN"},
            {"session_id", session_id}});
    }

    dnp3host::BackendOperationResult disconnect() override
    {
        ++disconnect_calls;
        if (!connected) {
            return dnp3host::BackendOperationResult::failure(
                dnp3host::ErrorCode::NotConnected, "not connected");
        }
        connected = false;
        return dnp3host::BackendOperationResult::success(
            dnp3host::Json{{"state", "READY"}, {"session_id", session_id}});
    }

    dnp3host::BackendOperationResult integrity_poll(
        const dnp3host::ReadOptions& options) override
    {
        ++integrity_poll_calls;
        last_read_options = options;
        return read_result("integrity_poll");
    }

    dnp3host::BackendOperationResult class_poll(
        const dnp3host::ClassPollConfig& config) override
    {
        ++class_poll_calls;
        last_class_poll_config = config;
        return read_result("class_poll");
    }

    dnp3host::BackendOperationResult read(
        const dnp3host::ReadConfig& config) override
    {
        ++read_calls;
        last_read_config = config;
        return read_result("read");
    }

    dnp3host::BackendOperationResult enable_unsolicited(
        const dnp3host::UnsolicitedControlConfig& config) override
    {
        ++enable_unsolicited_calls;
        last_unsolicited_control_config = config;
        return unsolicited_control_result("enable");
    }

    dnp3host::BackendOperationResult disable_unsolicited(
        const dnp3host::UnsolicitedControlConfig& config) override
    {
        ++disable_unsolicited_calls;
        last_unsolicited_control_config = config;
        return unsolicited_control_result("disable");
    }

    dnp3host::BackendOperationResult wait_unsolicited(
        const dnp3host::WaitUnsolicitedConfig& config) override
    {
        last_wait_unsolicited_config = config;
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"session_id", session_id},
            {"enabled", true},
            {"classes", dnp3host::Json::array({1, 2, 3})},
            {"measurements", dnp3host::Json::array()},
            {"timed_out", true},
            {"summary", dnp3host::Json::object()}});
    }

    dnp3host::BackendOperationResult capture_begin(
        const dnp3host::CaptureConfig& config) override
    {
        ++capture_begin_calls;
        last_capture_config = config;
        return capture_result("ACTIVE");
    }

    dnp3host::BackendOperationResult capture_progress(
        const dnp3host::CaptureReferenceConfig& config) override
    {
        ++capture_progress_calls;
        last_capture_reference = config;
        return capture_result("ACTIVE");
    }

    dnp3host::BackendOperationResult capture_end(
        const dnp3host::CaptureReferenceConfig& config) override
    {
        ++capture_end_calls;
        last_capture_reference = config;
        return capture_result("FINALIZED");
    }

    dnp3host::BackendOperationResult select_and_operate(
        const dnp3host::CommandConfig& config) override
    {
        ++select_and_operate_calls;
        last_command_config = config;
        return command_result("select_and_operate", config.commands.size());
    }

    dnp3host::BackendOperationResult trace_start(
        const dnp3host::TraceStartConfig& config) override
    {
        return trace.start(config);
    }

    dnp3host::BackendOperationResult trace_read(
        const dnp3host::TraceReferenceConfig& config) override
    {
        return trace.read(config);
    }

    dnp3host::BackendOperationResult trace_stop(
        const dnp3host::TraceReferenceConfig& config) override
    {
        return trace.stop(config);
    }

    dnp3host::BackendOperationResult direct_operate(
        const dnp3host::CommandConfig& config) override
    {
        ++direct_operate_calls;
        last_command_config = config;
        return command_result("direct_operate", config.commands.size());
    }

    dnp3host::BackendOperationResult wait_event(
        const dnp3host::WaitEventConfig& config) override
    {
        last_wait_config = config;
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"events", dnp3host::Json::array()},
            {"timed_out", true},
            {"remaining", 0},
            {"dropped_total", 0}});
    }

    void shutdown() noexcept override
    {
        shutdown_called = true;
        connected = false;
    }

    bool connected{false};
    dnp3host::ProtocolTrace trace;
    bool shutdown_called{false};
    int connect_calls{0};
    int disconnect_calls{0};
    int integrity_poll_calls{0};
    int class_poll_calls{0};
    int read_calls{0};
    int select_and_operate_calls{0};
    int direct_operate_calls{0};
    int enable_unsolicited_calls{0};
    int disable_unsolicited_calls{0};
    int capture_begin_calls{0};
    int capture_progress_calls{0};
    int capture_end_calls{0};
    std::uint64_t session_id{0};
    std::optional<dnp3host::ConnectionConfig> last_config;
    std::optional<dnp3host::WaitEventConfig> last_wait_config;
    std::optional<dnp3host::ReadOptions> last_read_options;
    std::optional<dnp3host::ClassPollConfig> last_class_poll_config;
    std::optional<dnp3host::ReadConfig> last_read_config;
    std::optional<dnp3host::UnsolicitedControlConfig>
        last_unsolicited_control_config;
    std::optional<dnp3host::WaitUnsolicitedConfig> last_wait_unsolicited_config;
    std::optional<dnp3host::CaptureConfig> last_capture_config;
    std::optional<dnp3host::CaptureReferenceConfig> last_capture_reference;
    std::optional<dnp3host::CommandConfig> last_command_config;

private:
    dnp3host::BackendOperationResult capture_result(const char* state)
    {
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"capture_id", "cap-1-1"},
            {"session_id", session_id},
            {"state", state},
            {"valid", state == std::string_view{"FINALIZED"}
                 ? dnp3host::Json(true)
                 : dnp3host::Json(nullptr)}});
    }

    dnp3host::BackendOperationResult read_result(const char* operation)
    {
        if (!connected) {
            return dnp3host::BackendOperationResult::failure(
                dnp3host::ErrorCode::NotConnected, "not connected");
        }
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"operation", operation},
            {"task_id", 1},
            {"task_status", "SUCCESS"},
            {"measurements", dnp3host::Json::array()}});
    }

    dnp3host::BackendOperationResult command_result(
        const char* operation, const std::size_t points)
    {
        if (!connected) {
            return dnp3host::BackendOperationResult::failure(
                dnp3host::ErrorCode::NotConnected, "not connected");
        }
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"mode", operation},
            {"task_id", 2},
            {"task_status", "SUCCESS"},
            {"all_success", true},
            {"requested_points", points}});
    }

    dnp3host::BackendOperationResult unsolicited_control_result(
        const char* action)
    {
        if (!connected) {
            return dnp3host::BackendOperationResult::failure(
                dnp3host::ErrorCode::NotConnected, "not connected");
        }
        return dnp3host::BackendOperationResult::success(dnp3host::Json{
            {"action", action},
            {"task_id", 3},
            {"task_status", "SUCCESS"},
            {"classes", dnp3host::Json::array({1, 2, 3})}});
    }
};

void check(const bool condition, const std::string_view message)
{
    if (!condition) {
        ++failures;
        std::cerr << "FAILED: " << message << '\n';
    }
}

void check_parse_error(
    const std::string& line,
    const dnp3host::ErrorCode expected_code,
    const std::string_view expected_reason)
{
    const auto parsed = dnp3host::JsonLineProtocol::parse_request(line);
    check(!parsed.request.has_value(), "invalid input must not produce a request");
    check(parsed.error.has_value(), "invalid input must produce an error");
    if (!parsed.error) {
        return;
    }
    check(parsed.error->code == expected_code, "parse error code must be stable");
    if (!expected_reason.empty()) {
        check(
            parsed.error->details.value("reason", "") == expected_reason,
            "parse error reason must be stable");
    }
}

void test_line_reader()
{
    std::istringstream crlf{"1234\r\n"};
    const auto compatible = dnp3host::JsonLineProtocol::read_line(crlf, 4);
    check(compatible.status == dnp3host::ReadLineStatus::Line, "CRLF must be accepted");
    check(compatible.line == "1234", "CR must not be part of the request");

    std::istringstream oversized{"12345\nnext\n"};
    const auto rejected = dnp3host::JsonLineProtocol::read_line(oversized, 4);
    check(
        rejected.status == dnp3host::ReadLineStatus::TooLarge,
        "oversized request must be rejected");
    const auto resynchronized = dnp3host::JsonLineProtocol::read_line(oversized, 4);
    check(
        resynchronized.status == dnp3host::ReadLineStatus::Line
            && resynchronized.line == "next",
        "reader must resume at the next line after an oversized request");

    std::istringstream no_final_newline{"last"};
    const auto last = dnp3host::JsonLineProtocol::read_line(no_final_newline, 4);
    check(last.status == dnp3host::ReadLineStatus::Line, "EOF may terminate the last line");
    check(last.line == "last", "EOF-terminated line must be preserved");
    check(
        dnp3host::JsonLineProtocol::read_line(no_final_newline, 4).status
            == dnp3host::ReadLineStatus::EndOfFile,
        "subsequent read must report EOF");
}

void test_strict_parser()
{
    const auto valid = dnp3host::JsonLineProtocol::parse_request(
        R"({"schema_version":1,"id":"req-1","cmd":"hello","params":{}})");
    check(valid.request.has_value(), "valid request must parse");
    check(!valid.error.has_value(), "valid request must not produce an error");
    if (valid.request) {
        check(valid.request->id == "req-1", "request id must be preserved");
        check(valid.request->command == "hello", "command must be preserved");
    }

    check_parse_error("", dnp3host::ErrorCode::InvalidRequest, "empty_line");
    check_parse_error("{", dnp3host::ErrorCode::InvalidRequest, "invalid_json");
    check_parse_error("[]", dnp3host::ErrorCode::InvalidRequest, "root_not_object");
    check_parse_error(
        std::string{"\xEF\xBB\xBF"}
            + R"({"schema_version":1,"id":"a","cmd":"hello","params":{}})",
        dnp3host::ErrorCode::InvalidRequest,
        "utf8_bom");
    check_parse_error(
        std::string{"\xC3\x28", 2},
        dnp3host::ErrorCode::InvalidRequest,
        "invalid_utf8");
    check_parse_error(
        R"({"schema_version":1,"id":"a","id":"b","cmd":"hello","params":{}})",
        dnp3host::ErrorCode::InvalidRequest,
        "duplicate_key");
    check_parse_error(
        R"({"schema_version":1,"id":"a","cmd":"hello","params":{"x":1,"x":2}})",
        dnp3host::ErrorCode::InvalidRequest,
        "duplicate_key");
    check_parse_error(
        R"({"schema_version":2,"id":"a","cmd":"hello","params":{}})",
        dnp3host::ErrorCode::SchemaMismatch,
        "");
    check_parse_error(
        R"({"schema_version":1,"id":"a","cmd":"hello","params":{},"extra":1})",
        dnp3host::ErrorCode::InvalidRequest,
        "unknown_field");
    check_parse_error(
        R"({"schema_version":1,"id":"a","cmd":"hello","params":[]})",
        dnp3host::ErrorCode::InvalidRequest,
        "invalid_params");

    std::string too_deep(65, '[');
    too_deep.append(65, ']');
    check_parse_error(
        too_deep, dnp3host::ErrorCode::InvalidRequest, "json_too_deep");
}

void test_connection_config_contract()
{
    dnp3host::ConnectionConfig minimal;
    auto error = dnp3host::parse_connection_config(
        dnp3host::Json{{"host", "127.0.0.1"}}, minimal);
    check(!error.has_value(), "minimal TCP connection configuration must be valid");
    check(minimal.port == 20000, "DNP3 TCP port must default to 20000");
    check(minimal.master_address == 1, "master link address must have a stable default");
    check(
        minimal.outstation_address == 1024,
        "outstation link address must have a stable default");

    dnp3host::ConnectionConfig boundary;
    error = dnp3host::parse_connection_config(
        dnp3host::Json{
            {"host", "example.invalid"},
            {"port", 65535},
            {"connect_timeout_ms", 50},
            {"retry", dnp3host::Json{{"min_ms", 10}, {"max_ms", 300000}}},
            {"link",
             dnp3host::Json{{"master_address", 0},
                            {"outstation_address", 65519},
                            {"keep_alive_timeout_ms", 86400000}}}},
        boundary);
    check(!error.has_value(), "documented upper and lower bounds must be accepted");
    check(boundary.outstation_address == 65519, "last individual DNP3 address must be accepted");

    dnp3host::ConnectionConfig lab;
    error = dnp3host::parse_connection_config(
        dnp3host::Json{
            {"host", "127.0.0.1"},
            {"safety",
             dnp3host::Json{{"environment", "LAB"},
                            {"allow_state_change", true},
                            {"operator_id", "pytest-operator"},
                            {"dut_id", "simulated-dut"}}}},
        lab);
    check(!error.has_value(), "explicit LAB authorization must parse");
    check(lab.allow_state_change, "LAB state-change authorization must be preserved");

    dnp3host::ConnectionConfig simulator;
    error = dnp3host::parse_connection_config(
        dnp3host::Json{{"host", "127.0.0.1"},
                       {"safety", dnp3host::Json{{"environment", "SIMULATOR"}}}},
        simulator);
    check(!error.has_value(), "simulator mode requires no operator or DUT identity");
    check(simulator.allow_state_change, "simulator controls must be enabled");
    check(simulator.operator_id.empty() && simulator.dut_id.empty(),
        "simulator mode must not manufacture LAB identities");

    const auto expect_error = [](const dnp3host::Json& params,
                                 const std::string_view field,
                                 const std::string_view reason) {
        dnp3host::ConnectionConfig output;
        const auto parsed_error = dnp3host::parse_connection_config(params, output);
        check(parsed_error.has_value(), "invalid connection config must fail");
        if (parsed_error) {
            check(
                parsed_error->code == dnp3host::ErrorCode::InvalidRequest,
                "invalid connection config must use INVALID_REQUEST");
            check(
                parsed_error->details.value("field", "") == field,
                "invalid connection config must identify its field");
            check(
                parsed_error->details.value("reason", "") == reason,
                "invalid connection config must expose a stable reason");
        }
    };

    expect_error(dnp3host::Json::object(), "host", "missing_field");
    expect_error(
        dnp3host::Json{{"host", "127.0.0.1"},
                       {"safety", dnp3host::Json{{"environment", "SIMULATOR"},
                                               {"allow_state_change", false}}}},
        "safety", "simulator_accepts_environment_only");
    expect_error(
        dnp3host::Json{{"host", "127.0.0.1"}, {"extra", true}},
        "extra",
        "unknown_field");
    expect_error(
        dnp3host::Json{{"host", "127.0.0.1"},
                       {"link", dnp3host::Json{{"outstation_address", 65520}}}},
        "link.outstation_address",
        "out_of_range");
    expect_error(
        dnp3host::Json{{"host", "127.0.0.1"},
                       {"retry", dnp3host::Json{{"min_ms", 100}, {"max_ms", 99}}}},
        "retry",
        "invalid_order");
    expect_error(
        dnp3host::Json{{"host", "127.0.0.1"},
                       {"link",
                        dnp3host::Json{{"master_address", 7},
                                       {"outstation_address", 7}}}},
        "link",
        "addresses_must_differ");
    expect_error(
        dnp3host::Json{
            {"host", "127.0.0.1"},
            {"safety",
             dnp3host::Json{{"environment", "PRODUCTION"},
                            {"allow_state_change", true},
                            {"operator_id", "operator"},
                            {"dut_id", "dut"}}}},
        "safety.environment",
        "state_change_requires_lab");

    dnp3host::WaitEventConfig wait_config;
    auto wait_error = dnp3host::parse_wait_event_config(
        dnp3host::Json{{"timeout_ms", 60000}, {"max_events", 256}}, wait_config);
    check(!wait_error.has_value(), "wait_event documented bounds must be accepted");
    check(wait_config.timeout_ms == 60000, "wait timeout must be preserved");
    wait_error = dnp3host::parse_wait_event_config(
        dnp3host::Json{{"max_events", 0}}, wait_config);
    check(wait_error.has_value(), "wait_event must reject an empty batch limit");
}

void test_command_config_contract()
{
    const auto token = "0123456789abcdef0123456789abcdef";
    dnp3host::CommandConfig config;
    auto error = dnp3host::parse_command_config(
        dnp3host::Json{
            {"safety_token", token},
            {"timeout_ms", 50},
            {"response_mode", "response"},
            {"commands",
             dnp3host::Json::array(
                 {dnp3host::Json{{"type", "crob"},
                                 {"index", 1},
                                 {"operation", "pulse_on"},
                                 {"trip_close", "close"},
                                 {"count", 2},
                                 {"on_time_ms", 250},
                                 {"off_time_ms", 500}},
                  dnp3host::Json{{"type", "analog_output_int16"},
                                 {"index", 2},
                                 {"value", -32768}},
                  dnp3host::Json{{"type", "analog_output_int32"},
                                 {"index", 3},
                                 {"value", 2147483647}},
                  dnp3host::Json{{"type", "analog_output_float32"},
                                 {"index", 4},
                                 {"value", 1.25}},
                  dnp3host::Json{{"type", "analog_output_double64"},
                                 {"index", 5},
                                 {"value", -2.5}}})}},
        config);
    check(!error.has_value(), "mixed command batch must parse");
    check(config.commands.size() == 5, "every command point must be preserved");
    check(config.commands[0].count == 2, "CROB count must be preserved");
    check(config.commands[1].integer_value == -32768, "int16 value must be preserved");
    check(config.commands[3].floating_value == 1.25, "float value must be preserved");

    const auto expect_error = [](
                                  const dnp3host::Json& params,
                                  const std::string_view field,
                                  const std::string_view reason) {
        dnp3host::CommandConfig output;
        const auto parsed_error = dnp3host::parse_command_config(params, output);
        check(parsed_error.has_value(), "invalid command config must fail");
        if (parsed_error) {
            check(
                parsed_error->details.value("field", "") == field,
                "invalid command config must identify its field");
            check(
                parsed_error->details.value("reason", "") == reason,
                "invalid command config must expose a stable reason");
        }
    };

    expect_error(
        dnp3host::Json{{"commands", dnp3host::Json::array()}},
        "safety_token",
        "missing_field");
    expect_error(
        dnp3host::Json{{"safety_token", "not-a-token"},
                       {"commands", dnp3host::Json::array()}},
        "safety_token",
        "invalid_length");
    expect_error(
        dnp3host::Json{{"safety_token", token},
                       {"commands", dnp3host::Json::array()}},
        "commands",
        "invalid_length");
    expect_error(
        dnp3host::Json{
            {"safety_token", token},
            {"commands",
             dnp3host::Json::array(
                 {dnp3host::Json{{"type", "analog_output_int16"},
                                 {"index", 0},
                                 {"value", 32768}}})}},
        "commands[0].value",
        "out_of_range");
    expect_error(
        dnp3host::Json{
            {"safety_token", token},
            {"commands",
             dnp3host::Json::array(
                 {dnp3host::Json{{"type", "crob"},
                                 {"index", 7},
                                 {"operation", "latch_on"}},
                  dnp3host::Json{{"type", "crob"},
                                 {"index", 7},
                                 {"operation", "latch_off"}}})}},
        "commands[1]",
        "duplicate_command_point");
}

void test_read_config_contract()
{
    dnp3host::ReadOptions options;
    auto error = dnp3host::parse_read_options(
        dnp3host::Json{{"timeout_ms", 50},
                       {"max_measurements", 1000000},
                       {"return_mode", "summary"}},
        options);
    check(!error.has_value(), "documented read options must be accepted");
    check(options.timeout_ms == 50, "read timeout must be preserved");
    check(
        options.return_mode == dnp3host::ReturnMode::Summary,
        "summary return mode must be parsed");

    dnp3host::ClassPollConfig classes;
    error = dnp3host::parse_class_poll_config(
        dnp3host::Json{{"classes", dnp3host::Json::array({1, 3})}}, classes);
    check(!error.has_value(), "Class 1/3 poll must be accepted");
    check(classes.class_mask == 0x0A, "class list must map to the OpenDNP3 bit mask");

    dnp3host::ReadConfig read;
    error = dnp3host::parse_read_config(
        dnp3host::Json{
            {"headers",
             dnp3host::Json::array(
                 {dnp3host::Json{{"group", 30},
                                 {"variation", 0},
                                 {"qualifier", "all_objects"}},
                  dnp3host::Json{{"group", 1},
                                 {"variation", 2},
                                 {"qualifier", "range16"},
                                 {"start", 0},
                                 {"stop", 999}},
                  dnp3host::Json{{"group", 60},
                                 {"variation", 2},
                                 {"qualifier", "count8"},
                                 {"count", 10}}})}},
        read);
    check(!error.has_value(), "multi-header read must be accepted");
    check(read.headers.size() == 3, "all read headers must be preserved");
    check(
        read.headers[1].qualifier == dnp3host::ReadQualifier::Range16,
        "range16 qualifier must use the fixed enum");

    const auto expect_error = [](const dnp3host::Json& params,
                                 const std::string_view field,
                                 const std::string_view reason) {
        dnp3host::ReadConfig output;
        const auto parsed_error = dnp3host::parse_read_config(params, output);
        check(parsed_error.has_value(), "invalid read config must fail");
        if (parsed_error) {
            check(
                parsed_error->details.value("field", "") == field,
                "invalid read config must identify its field");
            check(
                parsed_error->details.value("reason", "") == reason,
                "invalid read config must expose a stable reason");
        }
    };
    expect_error(dnp3host::Json::object(), "headers", "missing_field");
    expect_error(
        dnp3host::Json{{"headers", dnp3host::Json::array()}},
        "headers",
        "invalid_length");
    expect_error(
        dnp3host::Json{
            {"headers",
             dnp3host::Json::array({dnp3host::Json{{"group", 1},
                                                  {"variation", 2},
                                                  {"qualifier", "range8"},
                                                  {"start", 5},
                                                  {"stop", 4}}})}},
        "headers[0]",
        "invalid_range");
    expect_error(
        dnp3host::Json{
            {"headers",
             dnp3host::Json::array({dnp3host::Json{{"group", 60},
                                                  {"variation", 2},
                                                  {"qualifier", "range16"},
                                                  {"start", 0},
                                                  {"stop", 1}}})}},
        "headers[0].qualifier",
        "unsupported_combination");

    dnp3host::ClassPollConfig duplicate_classes;
    const auto duplicate_error = dnp3host::parse_class_poll_config(
        dnp3host::Json{{"classes", dnp3host::Json::array({1, 1})}},
        duplicate_classes);
    check(duplicate_error.has_value(), "duplicate event classes must be rejected");
    if (duplicate_error) {
        check(
            duplicate_error->details.value("reason", "") == "duplicate_value",
            "duplicate event classes must expose a stable reason");
    }

    dnp3host::UnsolicitedControlConfig unsolicited;
    error = dnp3host::parse_unsolicited_control_config(
        dnp3host::Json{{"timeout_ms", 5000},
                       {"classes", dnp3host::Json::array({1, 2})}},
        unsolicited);
    check(!error.has_value(), "unsolicited control bounds must be accepted");
    check(
        unsolicited.class_mask == 0x06,
        "unsolicited classes must map to the OpenDNP3 bit mask");

    dnp3host::WaitUnsolicitedConfig wait_unsolicited;
    error = dnp3host::parse_wait_unsolicited_config(
        dnp3host::Json{{"timeout_ms", 60000}, {"max_events", 256}},
        wait_unsolicited);
    check(!error.has_value(), "wait_unsolicited bounds must be accepted");
    error = dnp3host::parse_wait_unsolicited_config(
        dnp3host::Json{{"max_events", 257}}, wait_unsolicited);
    check(error.has_value(), "wait_unsolicited must enforce its batch limit");
}

void test_controller()
{
    auto owned_backend = std::make_unique<FakeBackend>();
    auto* const backend = owned_backend.get();
    dnp3host::HostController controller{4096, std::move(owned_backend)};

    controller.record_request_received();
    const auto hello = controller.dispatch(
        dnp3host::Request{"hello-1", "hello", dnp3host::Json::object()});
    check(hello.response.at("ok") == true, "hello must succeed with an injected backend");
    check(hello.response.at("result").at("backend") == "fake", "backend must be honest");
    check(
        hello.response.at("result").at("backend_version") == "test-1",
        "backend version must be exposed");
    check(
        hello.response.at("result").at("capability_matrix_version") == "1",
        "hello must expose the capability matrix version");
    check(
        hello.response.at("result").at("supported_commands")
            == dnp3host::Json::array(
                {"capture.begin",
                 "capture.end",
                 "capture.progress",
                 "class_poll",
                 "connect",
                 "direct_operate",
                 "disable_unsolicited",
                 "disconnect",
                 "enable_unsolicited",
                 "get_status",
                 "hello",
                 "integrity_poll",
                 "read",
                 "select_and_operate",
                 "shutdown",
                 "stats",
                 "wait_event",
                 "wait_unsolicited"}),
        "hello must advertise the injected backend command set");

    controller.record_request_received();
    const auto duplicate = controller.dispatch(
        dnp3host::Request{"hello-1", "hello", dnp3host::Json::object()});
    check(duplicate.response.at("ok") == false, "duplicate request id must fail");
    check(
        duplicate.response.at("error").at("code") == "INVALID_REQUEST",
        "duplicate request id must use INVALID_REQUEST");
    check(
        duplicate.response.at("error").at("details").at("reason") == "duplicate_id",
        "duplicate request id must expose a stable reason");

    controller.record_request_received();
    const auto invalid_connect = controller.dispatch(
        dnp3host::Request{"connect-invalid", "connect", dnp3host::Json::object()});
    check(
        invalid_connect.response.at("error").at("details").at("field") == "host",
        "connect must validate parameters before invoking the backend");
    check(backend->connect_calls == 0, "invalid connect must not reach the backend");

    controller.record_request_received();
    const auto connected = controller.dispatch(dnp3host::Request{
        "connect-1", "connect", dnp3host::Json{{"host", "127.0.0.1"}}});
    check(connected.response.at("ok") == true, "valid connect must reach the backend");
    check(backend->connect_calls == 1, "connect must be dispatched exactly once");
    check(backend->last_config->port == 20000, "controller must apply TCP defaults");

    controller.record_request_received();
    const auto duplicate_connect = controller.dispatch(dnp3host::Request{
        "connect-2", "connect", dnp3host::Json{{"host", "127.0.0.1"}}});
    check(
        duplicate_connect.response.at("error").at("code") == "ALREADY_CONNECTED",
        "duplicate active session must use ALREADY_CONNECTED");

    controller.record_request_received();
    const auto status_connected = controller.dispatch(
        dnp3host::Request{"status-connected", "get_status", dnp3host::Json::object()});
    check(
        status_connected.response.at("result").at("state") == "CONNECTED",
        "OPEN backend channel must map to CONNECTED host state");

    controller.record_request_received();
    const auto waited = controller.dispatch(dnp3host::Request{
        "wait-1", "wait_event", dnp3host::Json{{"timeout_ms", 25}, {"max_events", 3}}});
    check(waited.response.at("ok") == true, "wait_event must reach the backend");
    check(backend->last_wait_config->timeout_ms == 25, "wait_event timeout must be preserved");
    check(backend->last_wait_config->max_events == 3, "wait_event batch limit must be preserved");

    controller.record_request_received();
    const auto integrity = controller.dispatch(dnp3host::Request{
        "integrity-1",
        "integrity_poll",
        dnp3host::Json{{"timeout_ms", 2500}, {"return_mode", "summary"}}});
    check(integrity.response.at("ok") == true, "integrity_poll must reach the backend");
    check(backend->integrity_poll_calls == 1, "integrity_poll must be dispatched once");
    check(
        backend->last_read_options->return_mode == dnp3host::ReturnMode::Summary,
        "integrity_poll return mode must be preserved");

    controller.record_request_received();
    const auto class_poll = controller.dispatch(dnp3host::Request{
        "class-1",
        "class_poll",
        dnp3host::Json{{"classes", dnp3host::Json::array({1, 2})}}});
    check(class_poll.response.at("ok") == true, "class_poll must reach the backend");
    check(
        backend->last_class_poll_config->class_mask == 0x06,
        "class_poll class mask must be preserved");

    controller.record_request_received();
    const auto read = controller.dispatch(dnp3host::Request{
        "read-1",
        "read",
        dnp3host::Json{
            {"headers",
             dnp3host::Json::array({dnp3host::Json{{"group", 30},
                                                  {"variation", 0},
                                                  {"qualifier", "all_objects"}}})}}});
    check(read.response.at("ok") == true, "read must reach the backend");
    check(backend->last_read_config->headers.size() == 1, "read headers must reach backend");

    controller.record_request_received();
    const auto enabled_unsolicited = controller.dispatch(dnp3host::Request{
        "unsol-enable-1",
        "enable_unsolicited",
        dnp3host::Json{{"classes", dnp3host::Json::array({1, 2})}}});
    check(
        enabled_unsolicited.response.at("ok") == true,
        "enable_unsolicited must reach the backend");
    check(
        backend->last_unsolicited_control_config->class_mask == 0x06,
        "enable_unsolicited class mask must be preserved");

    controller.record_request_received();
    const auto waited_unsolicited = controller.dispatch(dnp3host::Request{
        "unsol-wait-1",
        "wait_unsolicited",
        dnp3host::Json{{"timeout_ms", 25}, {"max_events", 7}}});
    check(
        waited_unsolicited.response.at("ok") == true,
        "wait_unsolicited must reach the backend");
    check(
        backend->last_wait_unsolicited_config->max_events == 7,
        "wait_unsolicited batch limit must be preserved");

    controller.record_request_received();
    const auto disabled_unsolicited = controller.dispatch(dnp3host::Request{
        "unsol-disable-1",
        "disable_unsolicited",
        dnp3host::Json{{"classes", dnp3host::Json::array({1, 2})}}});
    check(
        disabled_unsolicited.response.at("ok") == true,
        "disable_unsolicited must reach the backend");
    check(
        backend->disable_unsolicited_calls == 1,
        "disable_unsolicited must be dispatched once");

    controller.record_request_received();
    const auto control_params = dnp3host::Json{
        {"safety_token", "0123456789abcdef0123456789abcdef"},
        {"timeout_ms", 2000},
        {"commands",
         dnp3host::Json::array({dnp3host::Json{
             {"type", "crob"}, {"index", 7}, {"operation", "latch_on"}}})}};
    const auto control = controller.dispatch(dnp3host::Request{
        "control-1", "select_and_operate", control_params});
    check(control.response.at("ok") == true, "control must reach the backend");
    check(
        backend->select_and_operate_calls == 1,
        "select_and_operate must be dispatched once");
    check(
        backend->last_command_config->commands.front().index == 7,
        "control point index must reach the backend");

    controller.record_request_received();
    const auto stats = controller.dispatch(
        dnp3host::Request{"stats-1", "stats", dnp3host::Json::object()});
    check(stats.response.at("ok") == true, "stats must be available without a session");
    check(
        stats.response.at("result").at("scope") == "host_channel_and_local_queues",
        "stats must state its implemented scope");

    controller.record_request_received();
    const auto disconnected = controller.dispatch(
        dnp3host::Request{"disconnect-1", "disconnect", dnp3host::Json::object()});
    check(disconnected.response.at("ok") == true, "disconnect must reach the backend");

    controller.record_request_received();
    const auto duplicate_disconnect = controller.dispatch(
        dnp3host::Request{"disconnect-2", "disconnect", dnp3host::Json::object()});
    check(
        duplicate_disconnect.response.at("error").at("code") == "NOT_CONNECTED",
        "disconnect without an active session must use NOT_CONNECTED");

    controller.record_request_received();
    const auto capture_begin = controller.dispatch(dnp3host::Request{
        "capture-1",
        "capture.begin",
        dnp3host::Json{
            {"mode", "static_set"},
            {"sources", dnp3host::Json::array({"solicited"})},
            {"duration_limit_ms", 1000},
            {"expected",
             dnp3host::Json{{"point_ranges",
                             dnp3host::Json::array({dnp3host::Json{
                                 {"kind", "analog_input"},
                                 {"start", 0},
                                 {"stop", 9}}})}}}}});
    check(capture_begin.response.at("ok") == true, "capture.begin must reach backend");
    check(backend->capture_begin_calls == 1, "capture.begin must be dispatched once");
    check(
        backend->last_capture_config->point_ranges.front().stop == 9,
        "capture expected range must be preserved");

    controller.record_request_received();
    const auto capture_progress = controller.dispatch(dnp3host::Request{
        "capture-2",
        "capture.progress",
        dnp3host::Json{{"capture_id", "cap-1-1"}}});
    check(
        capture_progress.response.at("ok") == true,
        "capture.progress must reach backend");

    controller.record_request_received();
    const auto capture_end = controller.dispatch(dnp3host::Request{
        "capture-3",
        "capture.end",
        dnp3host::Json{{"capture_id", "cap-1-1"}, {"drain_timeout_ms", 1000}}});
    check(capture_end.response.at("ok") == true, "capture.end must reach backend");
    check(
        backend->last_capture_reference->drain_timeout_ms == 1000,
        "capture.end drain timeout must be preserved");

    controller.record_request_received();
    const auto unknown = controller.dispatch(
        dnp3host::Request{"unknown-1", "not_a_command", dnp3host::Json::object()});
    check(
        unknown.response.at("error").at("details").at("reason") == "unknown_command",
        "unknown command must expose a stable reason");

    controller.record_request_received();
    const auto status = controller.dispatch(
        dnp3host::Request{"status-1", "get_status", dnp3host::Json::object()});
    check(status.response.at("result").at("state") == "READY", "host must be ready");
    check(
        status.response.at("result").at("metrics").at("requests_received") == 22,
        "status must report received requests");

    controller.record_request_received();
    const auto shutdown = controller.dispatch(
        dnp3host::Request{"shutdown-1", "shutdown", dnp3host::Json::object()});
    check(shutdown.should_stop, "shutdown must request process termination");
    check(
        shutdown.response.at("result").at("state") == "SHUTTING_DOWN",
        "shutdown response must expose the terminal state");
    check(backend->shutdown_called, "shutdown must propagate to the backend");

    const auto after_shutdown = controller.dispatch(
        dnp3host::Request{"late-1", "hello", dnp3host::Json::object()});
    check(after_shutdown.should_stop, "requests after shutdown must retain stop state");
    check(
        after_shutdown.response.at("error").at("code") == "PROCESS_SHUTTING_DOWN",
        "requests after shutdown must be rejected deterministically");
}

void test_response_serialization()
{
    const auto response = dnp3host::JsonLineProtocol::success_response(
        "id-1", dnp3host::Json{{"value", 7}});
    const auto serialized = dnp3host::JsonLineProtocol::serialize(response);
    check(serialized.find('\n') == std::string::npos, "one response must serialize to one line");
    check(dnp3host::Json::parse(serialized) == response, "serialized response must be valid JSON");
}

}  // namespace

int main()
{
    test_line_reader();
    test_strict_parser();
    test_connection_config_contract();
    test_read_config_contract();
    test_command_config_contract();
    test_controller();
    test_response_serialization();
    if (failures != 0) {
        std::cerr << failures << " protocol assertion(s) failed\n";
        return 1;
    }
    return 0;
}
