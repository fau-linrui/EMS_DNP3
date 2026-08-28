#include "dnp3host/Backend.h"
#include "dnp3host/ConnectionConfig.h"
#include "dnp3host/HostController.h"
#include "dnp3host/JsonLineProtocol.h"
#include "dnp3host/Models.h"

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
        return {"connect", "disconnect", "get_status", "hello", "shutdown", "wait_event"};
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
    bool shutdown_called{false};
    int connect_calls{0};
    int disconnect_calls{0};
    std::uint64_t session_id{0};
    std::optional<dnp3host::ConnectionConfig> last_config;
    std::optional<dnp3host::WaitEventConfig> last_wait_config;
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

    dnp3host::WaitEventConfig wait_config;
    auto wait_error = dnp3host::parse_wait_event_config(
        dnp3host::Json{{"timeout_ms", 60000}, {"max_events", 256}}, wait_config);
    check(!wait_error.has_value(), "wait_event documented bounds must be accepted");
    check(wait_config.timeout_ms == 60000, "wait timeout must be preserved");
    wait_error = dnp3host::parse_wait_event_config(
        dnp3host::Json{{"max_events", 0}}, wait_config);
    check(wait_error.has_value(), "wait_event must reject an empty batch limit");
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
                {"connect", "disconnect", "get_status", "hello", "shutdown", "wait_event"}),
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
    const auto backend_command = controller.dispatch(
        dnp3host::Request{"read-1", "read", dnp3host::Json::object()});
    check(
        backend_command.response.at("error").at("code") == "UNSUPPORTED_BY_BACKEND",
        "future backend commands must not report success");
    check(
        backend_command.response.at("error").at("details").at("backend") == "fake",
        "unsupported command must identify the active backend");

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
        status.response.at("result").at("metrics").at("requests_received") == 12,
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
    test_controller();
    test_response_serialization();
    if (failures != 0) {
        std::cerr << failures << " protocol assertion(s) failed\n";
        return 1;
    }
    return 0;
}
