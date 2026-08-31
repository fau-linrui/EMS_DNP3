#include "dnp3host/HostController.h"

#include "dnp3host/ConnectionConfig.h"
#include "dnp3host/CaptureConfig.h"
#include "dnp3host/CommandConfig.h"
#include "dnp3host/JsonLineProtocol.h"
#include "dnp3host/OpenDnp3Backend.h"
#include "dnp3host/ProjectInfo.h"
#include "dnp3host/ReadConfig.h"

#include <algorithm>
#include <array>
#include <utility>

namespace dnp3host {

HostController::HostController(
    const std::size_t max_request_bytes, std::unique_ptr<IMasterBackend> backend)
    : max_request_bytes_(max_request_bytes),
      backend_(backend ? std::move(backend) : make_opendnp3_backend())
{
    recent_request_id_set_.reserve(kRecentRequestIdCapacity);
}

HostController::~HostController()
{
    backend_->shutdown();
}

void HostController::record_request_received() noexcept
{
    ++requests_received_;
}

void HostController::record_protocol_failure() noexcept
{
    ++requests_failed_;
}

DispatchResult HostController::dispatch(const Request& request)
{
    if (state_ == HostState::ShuttingDown) {
        ++requests_failed_;
        return DispatchResult{
            JsonLineProtocol::error_response(ProtocolError{
                request.id,
                ErrorCode::ProcessShuttingDown,
                "host process is shutting down",
                Json::object()}),
            true};
    }

    if (!remember_request_id(request.id)) {
        ++requests_failed_;
        return DispatchResult{
            JsonLineProtocol::error_response(ProtocolError{
                request.id,
                ErrorCode::InvalidRequest,
                "request id was already used recently",
                Json{{"reason", "duplicate_id"},
                     {"recent_id_capacity", kRecentRequestIdCapacity}}}),
            false};
    }

    if (!request.params.empty()
        && (request.command == "hello" || request.command == "get_status"
            || request.command == "stats" || request.command == "shutdown"
            || request.command == "disconnect")) {
        ++requests_failed_;
        return DispatchResult{
            JsonLineProtocol::error_response(ProtocolError{
                request.id,
                ErrorCode::InvalidRequest,
                "command does not accept parameters",
                Json{{"reason", "unexpected_params"}, {"cmd", request.command}}}),
            false};
    }

    if (request.command == "hello") {
        ++requests_succeeded_;
        return DispatchResult{
            JsonLineProtocol::success_response(request.id, hello_result()), false};
    }
    if (request.command == "get_status") {
        ++requests_succeeded_;
        return DispatchResult{
            JsonLineProtocol::success_response(request.id, status_result()), false};
    }
    if (request.command == "stats") {
        const auto status = status_result();
        ++requests_succeeded_;
        return DispatchResult{
            JsonLineProtocol::success_response(
                request.id,
                Json{{"scope", "host_channel_and_local_queues"},
                     {"host", status.at("metrics")},
                     {"channel", status.at("channel")},
                     {"safety", status.at("safety")},
                     {"capture", status.at("capture")},
                     {"limitations",
                      Json::array({
                          "network byte counters are not exposed by OpenDNP3 3.1.2"})}}),
            false};
    }
    if (request.command == "shutdown") {
        state_ = HostState::ShuttingDown;
        backend_->shutdown();
        ++requests_succeeded_;
        return DispatchResult{
            JsonLineProtocol::success_response(
                request.id, Json{{"state", "SHUTTING_DOWN"}}),
            true};
    }
    if (request.command == "connect") {
        ConnectionConfig config;
        if (const auto error = parse_connection_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->connect(config));
    }
    if (request.command == "disconnect") {
        return backend_result(request.id, backend_->disconnect());
    }
    if (request.command == "integrity_poll") {
        ReadOptions options;
        if (const auto error = parse_read_options(request.params, options)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->integrity_poll(options));
    }
    if (request.command == "class_poll") {
        ClassPollConfig config;
        if (const auto error = parse_class_poll_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->class_poll(config));
    }
    if (request.command == "read") {
        ReadConfig config;
        if (const auto error = parse_read_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->read(config));
    }
    if (request.command == "enable_unsolicited"
        || request.command == "disable_unsolicited") {
        UnsolicitedControlConfig config;
        if (const auto error = parse_unsolicited_control_config(
                request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return request.command == "enable_unsolicited"
            ? backend_result(request.id, backend_->enable_unsolicited(config))
            : backend_result(request.id, backend_->disable_unsolicited(config));
    }
    if (request.command == "select_and_operate"
        || request.command == "direct_operate") {
        CommandConfig config;
        if (const auto error = parse_command_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return request.command == "select_and_operate"
            ? backend_result(request.id, backend_->select_and_operate(config))
            : backend_result(request.id, backend_->direct_operate(config));
    }
    if (request.command == "wait_event") {
        WaitEventConfig config;
        if (const auto error = parse_wait_event_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->wait_event(config));
    }
    if (request.command == "wait_unsolicited") {
        WaitUnsolicitedConfig config;
        if (const auto error = parse_wait_unsolicited_config(
                request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->wait_unsolicited(config));
    }
    if (request.command == "capture.begin") {
        CaptureConfig config;
        if (const auto error = parse_capture_config(request.params, config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return backend_result(request.id, backend_->capture_begin(config));
    }
    if (request.command == "capture.progress" || request.command == "capture.end") {
        CaptureReferenceConfig config;
        if (const auto error = parse_capture_reference_config(
                request.params, request.command == "capture.end", config)) {
            ++requests_failed_;
            return DispatchResult{
                JsonLineProtocol::error_response(ProtocolError{
                    request.id, error->code, error->message, error->details}),
                false};
        }
        return request.command == "capture.progress"
            ? backend_result(request.id, backend_->capture_progress(config))
            : backend_result(request.id, backend_->capture_end(config));
    }
    if (is_known_backend_command(request.command)) {
        ++requests_failed_;
        return DispatchResult{
            JsonLineProtocol::error_response(ProtocolError{
                request.id,
                ErrorCode::UnsupportedByBackend,
                "command is not implemented by the active DNP3 backend",
                Json{{"backend", backend_->name()}, {"cmd", request.command}}}),
            false};
    }

    ++requests_failed_;
    return DispatchResult{
        JsonLineProtocol::error_response(ProtocolError{
            request.id,
            ErrorCode::InvalidRequest,
            "request command is unknown",
            Json{{"reason", "unknown_command"}, {"cmd", request.command}}}),
        false};
}

DispatchResult HostController::backend_result(
    const std::string& request_id, BackendOperationResult operation)
{
    if (operation.error) {
        ++requests_failed_;
        return DispatchResult{
            JsonLineProtocol::error_response(ProtocolError{
                request_id,
                operation.error->code,
                std::move(operation.error->message),
                std::move(operation.error->details)}),
            false};
    }
    ++requests_succeeded_;
    return DispatchResult{
        JsonLineProtocol::success_response(request_id, std::move(operation.result)), false};
}

bool HostController::remember_request_id(const std::string& id)
{
    if (recent_request_id_set_.find(id) != recent_request_id_set_.end()) {
        return false;
    }
    if (recent_request_ids_.size() == kRecentRequestIdCapacity) {
        recent_request_id_set_.erase(recent_request_ids_.front());
        recent_request_ids_.pop_front();
    }
    recent_request_ids_.push_back(id);
    recent_request_id_set_.insert(id);
    return true;
}

Json HostController::hello_result() const
{
    const auto supported_commands = backend_->supported_commands();
    return Json{
        {"host_version", kVersion},
        {"backend", backend_->name()},
        {"backend_version", backend_->version()},
        {"git_commit", kGitCommit},
        {"platform", kPlatform},
        {"capability_matrix_version", kCapabilityMatrixVersion},
        {"capability_matrix_sha256", kCapabilityMatrixSha256},
        {"supported_commands", supported_commands},
        {"capabilities", backend_->capabilities()},
        {"limits",
         Json{
             {"max_request_bytes", max_request_bytes_},
             {"max_json_depth", JsonLineProtocol::kMaxJsonDepth},
             {"max_request_id_bytes", JsonLineProtocol::kMaxRequestIdBytes},
             {"recent_request_id_capacity", kRecentRequestIdCapacity}}}};
}

Json HostController::status_result() const
{
    const auto backend_status = backend_->status();
    return Json{
        {"state", state_name(backend_status)},
        {"backend", backend_->name()},
        {"in_flight_request", false},
        {"channel",
         Json{{"state", backend_status.channel_state},
              {"session_active", backend_status.session_active},
              {"session_id", backend_status.session_id},
              {"last_event_sequence", backend_status.last_event_sequence},
              {"queued_events", backend_status.queued_events},
              {"dropped_events", backend_status.dropped_events}}},
        {"safety",
         Json{{"state_change_authorized", backend_status.state_change_authorized},
              {"token_exposed", false}}},
        {"unsolicited",
         Json{{"enabled", backend_status.unsolicited_enabled},
              {"class_mask", backend_status.unsolicited_class_mask},
              {"last_receive_sequence",
               backend_status.unsolicited_last_sequence},
              {"queued_measurements",
               backend_status.queued_unsolicited_events},
              {"dropped_measurements",
               backend_status.dropped_unsolicited_events},
              {"fragments", backend_status.unsolicited_fragments}}},
        {"capture", backend_status.capture},
        {"metrics",
         Json{
             {"requests_received", requests_received_},
             {"requests_succeeded", requests_succeeded_},
             {"requests_failed", requests_failed_},
             {"recent_request_ids", recent_request_ids_.size()}}}};
}

const char* HostController::state_name(const BackendStatus& backend_status) const noexcept
{
    if (state_ == HostState::ShuttingDown) {
        return "SHUTTING_DOWN";
    }
    if (!backend_status.session_active) {
        return "READY";
    }
    return backend_status.channel_state == "OPEN" ? "CONNECTED" : "CONNECTING";
}

bool HostController::is_known_backend_command(const std::string& command)
{
    static constexpr std::array<const char*, 15> commands{
        "capture.begin",
        "capture.progress",
        "capture.end",
        "connect",
        "disconnect",
        "enable_unsolicited",
        "disable_unsolicited",
        "integrity_poll",
        "class_poll",
        "read",
        "direct_operate",
        "select_and_operate",
        "wait_event",
        "wait_unsolicited",
        "stats",
    };
    return std::any_of(
        commands.begin(), commands.end(), [&command](const char* candidate) {
            return command == candidate;
        });
}

}  // namespace dnp3host
