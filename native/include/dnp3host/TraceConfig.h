#pragma once

#include "dnp3host/Backend.h"

namespace dnp3host {

std::optional<BackendError> parse_trace_start_config(
    const Json& params, TraceStartConfig& output);
std::optional<BackendError> parse_trace_reference_config(
    const Json& params, bool accept_read_options, TraceReferenceConfig& output);

}  // namespace dnp3host
