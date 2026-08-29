#pragma once

#include "dnp3host/Backend.h"

#include <optional>

namespace dnp3host {

std::optional<BackendError> parse_command_config(
    const Json& params, CommandConfig& output);

}  // namespace dnp3host
