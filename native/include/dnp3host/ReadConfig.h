#pragma once

#include "dnp3host/Backend.h"

#include <optional>

namespace dnp3host {

std::optional<BackendError> parse_read_options(
    const Json& params, ReadOptions& output);
std::optional<BackendError> parse_class_poll_config(
    const Json& params, ClassPollConfig& output);
std::optional<BackendError> parse_read_config(
    const Json& params, ReadConfig& output);

}  // namespace dnp3host
