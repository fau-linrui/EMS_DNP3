#pragma once

#include "dnp3host/Backend.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>

namespace dnp3host {

class MeasurementCapture final {
public:
    MeasurementCapture();
    ~MeasurementCapture();

    MeasurementCapture(const MeasurementCapture&) = delete;
    MeasurementCapture& operator=(const MeasurementCapture&) = delete;

    BackendOperationResult begin(
        std::uint64_t session_id, const CaptureConfig& config);
    BackendOperationResult progress(const CaptureReferenceConfig& config);
    BackendOperationResult end(const CaptureReferenceConfig& config);

    Json abort(const std::string& reason) noexcept;
    Json status() const;

    void record_fragment(
        const std::string& source, std::uint64_t received_monotonic_ns) noexcept;
    void record_object(
        const std::string& source,
        const std::string& kind,
        std::uint8_t group,
        std::uint8_t variation,
        std::optional<std::uint16_t> index,
        const Json& value,
        std::uint64_t received_monotonic_ns) noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace dnp3host
