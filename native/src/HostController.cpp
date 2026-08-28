#include "dnp3host/HostController.h"

#include "dnp3host/ConnectionConfig.h"
#include "dnp3host/JsonLineProtocol.h"
#include "dnp3host/OpenDnp3Backend.h"
#include "dnp3host/ProjectInfo.h"

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
            || request.command == "shutdown" || request.command == "disconnect")) {
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
    static constexpr std::array<const char*, 9> commands{
        "connect",
        "disconnect",
        "integrity_poll",
        "class_poll",
        "read",
        "direct_operate",
        "select_and_operate",
        "wait_event",
        "stats",
    };
    return std::any_of(
        commands.begin(), commands.end(), [&command](const char* candidate) {
            return command == candidate;
        });
}

}  // namespace dnp3host
