#pragma once

#include "dnp3host/Backend.h"
#include "dnp3host/Models.h"

#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <string>
#include <unordered_set>

namespace dnp3host {

enum class HostState {
    Ready,
    ShuttingDown,
};

class HostController final {
public:
    static constexpr std::size_t kRecentRequestIdCapacity = 4096;

    explicit HostController(
        std::size_t max_request_bytes,
        std::unique_ptr<IMasterBackend> backend = nullptr);
    ~HostController();

    HostController(const HostController&) = delete;
    HostController& operator=(const HostController&) = delete;

    void record_request_received() noexcept;
    void record_protocol_failure() noexcept;
    DispatchResult dispatch(const Request& request);

private:
    bool remember_request_id(const std::string& id);
    DispatchResult backend_result(
        const std::string& request_id, BackendOperationResult operation);
    Json hello_result() const;
    Json status_result() const;
    const char* state_name(const BackendStatus& backend_status) const noexcept;
    static bool is_known_backend_command(const std::string& command);

    HostState state_{HostState::Ready};
    std::size_t max_request_bytes_;
    std::uint64_t requests_received_{0};
    std::uint64_t requests_succeeded_{0};
    std::uint64_t requests_failed_{0};
    std::deque<std::string> recent_request_ids_;
    std::unordered_set<std::string> recent_request_id_set_;
    std::unique_ptr<IMasterBackend> backend_;
};

}  // namespace dnp3host
