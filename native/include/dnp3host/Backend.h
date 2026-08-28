#pragma once

#include "dnp3host/Models.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace dnp3host {

struct ConnectionConfig {
    std::string host;
    std::uint16_t port{20000};
    std::string local_adapter{"0.0.0.0"};
    std::uint32_t connect_timeout_ms{5000};
    std::uint32_t retry_min_ms{1000};
    std::uint32_t retry_max_ms{60000};
    std::uint16_t master_address{1};
    std::uint16_t outstation_address{1024};
    std::uint32_t keep_alive_timeout_ms{60000};
};

struct WaitEventConfig {
    std::uint32_t timeout_ms{0};
    std::size_t max_events{64};
};

struct BackendError {
    ErrorCode code{ErrorCode::InternalError};
    std::string message;
    Json details{Json::object()};
};

struct BackendOperationResult {
    Json result{Json::object()};
    std::optional<BackendError> error;

    static BackendOperationResult success(Json value)
    {
        return BackendOperationResult{std::move(value), std::nullopt};
    }

    static BackendOperationResult failure(
        const ErrorCode code, std::string message, Json details = Json::object())
    {
        return BackendOperationResult{
            Json::object(), BackendError{code, std::move(message), std::move(details)}};
    }
};

struct BackendStatus {
    bool session_active{false};
    std::string channel_state{"CLOSED"};
    std::uint64_t session_id{0};
    std::uint64_t last_event_sequence{0};
    std::size_t queued_events{0};
    std::uint64_t dropped_events{0};
};

class IMasterBackend {
public:
    virtual ~IMasterBackend() = default;

    virtual std::string name() const = 0;
    virtual std::string version() const = 0;
    virtual std::vector<std::string> supported_commands() const = 0;
    virtual Json capabilities() const = 0;
    virtual BackendStatus status() const = 0;

    virtual BackendOperationResult connect(const ConnectionConfig& config) = 0;
    virtual BackendOperationResult disconnect() = 0;
    virtual BackendOperationResult wait_event(const WaitEventConfig& config) = 0;
    virtual void shutdown() noexcept = 0;
};

}  // namespace dnp3host
