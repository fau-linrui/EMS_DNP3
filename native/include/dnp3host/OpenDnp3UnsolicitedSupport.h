#pragma once

#include "dnp3host/Backend.h"

#include <cstddef>
#include <cstdint>
#include <memory>

namespace opendnp3 {
class IMaster;
class ISOEHandler;
}  // namespace opendnp3

namespace dnp3host {

struct UnsolicitedSnapshot {
    bool session_active{false};
    bool enabled{false};
    std::uint8_t class_mask{0};
    std::uint64_t last_sequence{0};
    std::size_t queued_events{0};
    std::uint64_t dropped_events{0};
    std::uint64_t fragments{0};
};

class OpenDnp3UnsolicitedSupport final {
public:
    static constexpr std::size_t kDefaultQueueCapacity = 4096;

    explicit OpenDnp3UnsolicitedSupport(
        std::size_t queue_capacity = kDefaultQueueCapacity);
    ~OpenDnp3UnsolicitedSupport();

    OpenDnp3UnsolicitedSupport(const OpenDnp3UnsolicitedSupport&) = delete;
    OpenDnp3UnsolicitedSupport& operator=(
        const OpenDnp3UnsolicitedSupport&) = delete;

    std::shared_ptr<opendnp3::ISOEHandler> handler() const;
    std::size_t capacity() const noexcept;

    void begin_session(std::uint64_t session_id);
    void end_session() noexcept;
    UnsolicitedSnapshot snapshot() const;

    BackendOperationResult enable(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const UnsolicitedControlConfig& config);
    BackendOperationResult disable(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const UnsolicitedControlConfig& config);
    BackendOperationResult wait(const WaitUnsolicitedConfig& config);

    void cancel_active() noexcept;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};

}  // namespace dnp3host
