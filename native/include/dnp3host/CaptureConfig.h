#pragma once

#include "dnp3host/Backend.h"

#include <optional>

namespace dnp3host {

std::optional<BackendError> parse_capture_config(
    const Json& params, CaptureConfig& output);

std::optional<BackendError> parse_capture_reference_config(
    const Json& params,
    bool accept_drain_timeout,
    CaptureReferenceConfig& output);

}  // namespace dnp3host
