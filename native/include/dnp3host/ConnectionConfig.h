#pragma once

#include "dnp3host/Backend.h"

#include <optional>

namespace dnp3host {

std::optional<BackendError> parse_connection_config(
    const Json& params, ConnectionConfig& output);

std::optional<BackendError> parse_wait_event_config(
    const Json& params, WaitEventConfig& output);

}  // namespace dnp3host
