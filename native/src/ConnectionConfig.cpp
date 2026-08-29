#include "dnp3host/ConnectionConfig.h"

#include <array>
#include <cctype>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>

namespace dnp3host {
namespace {

BackendError invalid_parameter(
    const std::string& field, const std::string& reason, Json details = Json::object())
{
    details["reason"] = reason;
    details["field"] = field;
    return BackendError{
        ErrorCode::InvalidRequest, "invalid command parameter", std::move(details)};
}

template <std::size_t Size>
std::optional<BackendError> reject_unknown_fields(
    const Json& value,
    const std::array<std::string_view, Size>& allowed,
    const std::string& prefix)
{
    for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
        bool found = false;
        for (const auto candidate : allowed) {
            if (iterator.key() == candidate) {
                found = true;
                break;
            }
        }
        if (!found) {
            const auto field = prefix.empty() ? iterator.key() : prefix + "." + iterator.key();
            return invalid_parameter(field, "unknown_field");
        }
    }
    return std::nullopt;
}

std::optional<BackendError> read_string(
    const Json& object,
    const char* key,
    const std::string& field,
    std::string& output,
    const bool required,
    const std::size_t maximum_bytes)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        if (required) {
            return invalid_parameter(field, "missing_field");
        }
        return std::nullopt;
    }
    if (!iterator->is_string()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "string"}});
    }

    const auto value = iterator->get<std::string>();
    if (value.empty() || value.size() > maximum_bytes) {
        return invalid_parameter(
            field,
            "invalid_length",
            Json{{"minimum_bytes", 1},
                 {"maximum_bytes", maximum_bytes},
                 {"received_bytes", value.size()}});
    }
    for (const auto character : value) {
        const auto byte = static_cast<unsigned char>(character);
        if (byte < 0x21U || byte > 0x7EU || std::isspace(byte) != 0) {
            return invalid_parameter(field, "invalid_character");
        }
    }
    output = value;
    return std::nullopt;
}

std::optional<BackendError> read_unsigned(
    const Json& object,
    const char* key,
    const std::string& field,
    std::uint64_t minimum,
    std::uint64_t maximum,
    std::uint64_t& output)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return std::nullopt;
    }
    if (!iterator->is_number_integer() && !iterator->is_number_unsigned()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "integer"}});
    }

    std::uint64_t value = 0;
    if (iterator->is_number_unsigned()) {
        value = iterator->get<std::uint64_t>();
    }
    else {
        const auto signed_value = iterator->get<std::int64_t>();
        if (signed_value < 0) {
            return invalid_parameter(
                field,
                "out_of_range",
                Json{{"minimum", minimum}, {"maximum", maximum}, {"received", signed_value}});
        }
        value = static_cast<std::uint64_t>(signed_value);
    }
    if (value < minimum || value > maximum) {
        return invalid_parameter(
            field,
            "out_of_range",
            Json{{"minimum", minimum}, {"maximum", maximum}, {"received", value}});
    }
    output = value;
    return std::nullopt;
}

std::optional<BackendError> require_object(
    const Json& object, const char* key, const std::string& field, const Json*& output)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        output = nullptr;
        return std::nullopt;
    }
    if (!iterator->is_object()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "object"}});
    }
    output = &(*iterator);
    return std::nullopt;
}

std::optional<BackendError> read_boolean(
    const Json& object,
    const char* key,
    const std::string& field,
    bool& output,
    const bool required)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        if (required) {
            return invalid_parameter(field, "missing_field");
        }
        return std::nullopt;
    }
    if (!iterator->is_boolean()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "boolean"}});
    }
    output = iterator->get<bool>();
    return std::nullopt;
}

}  // namespace

std::optional<BackendError> parse_connection_config(
    const Json& params, ConnectionConfig& output)
{
    static constexpr std::array<std::string_view, 7> root_fields{
        "host", "port", "local_adapter", "connect_timeout_ms", "retry", "link", "safety"};
    static constexpr std::array<std::string_view, 2> retry_fields{"min_ms", "max_ms"};
    static constexpr std::array<std::string_view, 3> link_fields{
        "master_address", "outstation_address", "keep_alive_timeout_ms"};
    static constexpr std::array<std::string_view, 4> safety_fields{
        "environment", "allow_state_change", "operator_id", "dut_id"};

    if (const auto error = reject_unknown_fields(params, root_fields, "")) {
        return error;
    }
    if (const auto error = read_string(params, "host", "host", output.host, true, 253)) {
        return error;
    }
    if (const auto error = read_string(
            params,
            "local_adapter",
            "local_adapter",
            output.local_adapter,
            false,
            64)) {
        return error;
    }

    std::uint64_t port = output.port;
    std::uint64_t connect_timeout = output.connect_timeout_ms;
    if (const auto error = read_unsigned(params, "port", "port", 1, 65535, port)) {
        return error;
    }
    if (const auto error = read_unsigned(
            params,
            "connect_timeout_ms",
            "connect_timeout_ms",
            50,
            300000,
            connect_timeout)) {
        return error;
    }
    output.port = static_cast<std::uint16_t>(port);
    output.connect_timeout_ms = static_cast<std::uint32_t>(connect_timeout);

    const Json* retry = nullptr;
    if (const auto error = require_object(params, "retry", "retry", retry)) {
        return error;
    }
    if (retry != nullptr) {
        if (const auto error = reject_unknown_fields(*retry, retry_fields, "retry")) {
            return error;
        }
        std::uint64_t minimum = output.retry_min_ms;
        std::uint64_t maximum = output.retry_max_ms;
        if (const auto error = read_unsigned(*retry, "min_ms", "retry.min_ms", 10, 300000, minimum)) {
            return error;
        }
        if (const auto error = read_unsigned(*retry, "max_ms", "retry.max_ms", 10, 300000, maximum)) {
            return error;
        }
        if (minimum > maximum) {
            return invalid_parameter(
                "retry",
                "invalid_order",
                Json{{"min_ms", minimum}, {"max_ms", maximum}});
        }
        output.retry_min_ms = static_cast<std::uint32_t>(minimum);
        output.retry_max_ms = static_cast<std::uint32_t>(maximum);
    }

    const Json* link = nullptr;
    if (const auto error = require_object(params, "link", "link", link)) {
        return error;
    }
    if (link != nullptr) {
        if (const auto error = reject_unknown_fields(*link, link_fields, "link")) {
            return error;
        }
        std::uint64_t master_address = output.master_address;
        std::uint64_t outstation_address = output.outstation_address;
        std::uint64_t keep_alive = output.keep_alive_timeout_ms;
        if (const auto error = read_unsigned(
                *link,
                "master_address",
                "link.master_address",
                0,
                65519,
                master_address)) {
            return error;
        }
        if (const auto error = read_unsigned(
                *link,
                "outstation_address",
                "link.outstation_address",
                0,
                65519,
                outstation_address)) {
            return error;
        }
        if (const auto error = read_unsigned(
                *link,
                "keep_alive_timeout_ms",
                "link.keep_alive_timeout_ms",
                1000,
                86400000,
                keep_alive)) {
            return error;
        }
        if (master_address == outstation_address) {
            return invalid_parameter(
                "link",
                "addresses_must_differ",
                Json{{"master_address", master_address},
                     {"outstation_address", outstation_address}});
        }
        output.master_address = static_cast<std::uint16_t>(master_address);
        output.outstation_address = static_cast<std::uint16_t>(outstation_address);
        output.keep_alive_timeout_ms = static_cast<std::uint32_t>(keep_alive);
    }

    const Json* safety = nullptr;
    if (const auto error = require_object(params, "safety", "safety", safety)) {
        return error;
    }
    if (safety != nullptr) {
        if (const auto error = reject_unknown_fields(*safety, safety_fields, "safety")) {
            return error;
        }
        if (const auto error = read_string(
                *safety,
                "environment",
                "safety.environment",
                output.safety_environment,
                true,
                16)) {
            return error;
        }
        if (output.safety_environment != "LAB") {
            return invalid_parameter(
                "safety.environment",
                "state_change_requires_lab",
                Json{{"received", output.safety_environment}});
        }
        if (const auto error = read_boolean(
                *safety,
                "allow_state_change",
                "safety.allow_state_change",
                output.allow_state_change,
                true)) {
            return error;
        }
        if (const auto error = read_string(
                *safety,
                "operator_id",
                "safety.operator_id",
                output.operator_id,
                true,
                128)) {
            return error;
        }
        if (const auto error = read_string(
                *safety,
                "dut_id",
                "safety.dut_id",
                output.dut_id,
                true,
                128)) {
            return error;
        }
    }

    return std::nullopt;
}

std::optional<BackendError> parse_wait_event_config(
    const Json& params, WaitEventConfig& output)
{
    static constexpr std::array<std::string_view, 2> fields{"timeout_ms", "max_events"};
    if (const auto error = reject_unknown_fields(params, fields, "")) {
        return error;
    }

    std::uint64_t timeout = output.timeout_ms;
    std::uint64_t maximum_events = output.max_events;
    if (const auto error = read_unsigned(
            params, "timeout_ms", "timeout_ms", 0, 60000, timeout)) {
        return error;
    }
    if (const auto error = read_unsigned(
            params, "max_events", "max_events", 1, 256, maximum_events)) {
        return error;
    }
    output.timeout_ms = static_cast<std::uint32_t>(timeout);
    output.max_events = static_cast<std::size_t>(maximum_events);
    return std::nullopt;
}

}  // namespace dnp3host
