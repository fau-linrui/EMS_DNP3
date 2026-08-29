#pragma once

#include "dnp3host/Backend.h"

#include <memory>

namespace opendnp3 {
class IMaster;
}

namespace dnp3host {

class OpenDnp3CommandSupport final {
public:
    OpenDnp3CommandSupport();
    ~OpenDnp3CommandSupport();

    OpenDnp3CommandSupport(const OpenDnp3CommandSupport&) = delete;
    OpenDnp3CommandSupport& operator=(const OpenDnp3CommandSupport&) = delete;

    BackendOperationResult select_and_operate(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const CommandConfig& config);
    BackendOperationResult direct_operate(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const CommandConfig& config);
    void cancel_active() noexcept;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};

}  // namespace dnp3host
