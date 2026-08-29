#pragma once

#include "dnp3host/Backend.h"

#include <memory>

namespace opendnp3 {
class IMaster;
class IMasterApplication;
}  // namespace opendnp3

namespace dnp3host {

class OpenDnp3ReadSupport final {
public:
    OpenDnp3ReadSupport();
    ~OpenDnp3ReadSupport();

    OpenDnp3ReadSupport(const OpenDnp3ReadSupport&) = delete;
    OpenDnp3ReadSupport& operator=(const OpenDnp3ReadSupport&) = delete;

    std::shared_ptr<opendnp3::IMasterApplication> master_application() const;

    BackendOperationResult integrity_poll(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const ReadOptions& options);
    BackendOperationResult class_poll(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const ClassPollConfig& config);
    BackendOperationResult read(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const ReadConfig& config);

    void cancel_active() noexcept;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};

}  // namespace dnp3host
