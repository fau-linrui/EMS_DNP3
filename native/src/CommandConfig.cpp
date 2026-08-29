#include "dnp3host/CommandConfig.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <set>
#include <string>
#include <string_view>
#include <utility>

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
            return invalid_parameter(
                prefix + "." + iterator.key(), "unknown_field");
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
    const std::size_t minimum,
    const std::size_t maximum)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return required
            ? std::optional<BackendError>{invalid_parameter(field, "missing_field")}
            : std::nullopt;
    }
    if (!iterator->is_string()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "string"}});
    }
    const auto value = iterator->get<std::string>();
    if (value.size() < minimum || value.size() > maximum) {
        return invalid_parameter(
            field,
            "invalid_length",
            Json{{"minimum_bytes", minimum},
                 {"maximum_bytes", maximum},
                 {"received_bytes", value.size()}});
    }
    output = value;
    return std::nullopt;
}

std::optional<BackendError> read_unsigned(
    const Json& object,
    const char* key,
    const std::string& field,
    const std::uint64_t minimum,
    const std::uint64_t maximum,
    std::uint64_t& output,
    const bool required)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return required
            ? std::optional<BackendError>{invalid_parameter(field, "missing_field")}
            : std::nullopt;
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

std::optional<BackendError> read_signed(
    const Json& object,
    const char* key,
    const std::string& field,
    const std::int64_t minimum,
    const std::int64_t maximum,
    std::int64_t& output)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return invalid_parameter(field, "missing_field");
    }
    if (!iterator->is_number_integer() && !iterator->is_number_unsigned()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "integer"}});
    }
    std::int64_t value = 0;
    if (iterator->is_number_unsigned()) {
        const auto unsigned_value = iterator->get<std::uint64_t>();
        if (unsigned_value > static_cast<std::uint64_t>(maximum)) {
            return invalid_parameter(
                field,
                "out_of_range",
                Json{{"minimum", minimum}, {"maximum", maximum}, {"received", unsigned_value}});
        }
        value = static_cast<std::int64_t>(unsigned_value);
    }
    else {
        value = iterator->get<std::int64_t>();
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

std::optional<BackendError> read_floating(
    const Json& object,
    const char* key,
    const std::string& field,
    const double minimum,
    const double maximum,
    double& output)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return invalid_parameter(field, "missing_field");
    }
    if (!iterator->is_number()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "number"}});
    }
    const auto value = iterator->get<double>();
    if (!std::isfinite(value) || value < minimum || value > maximum) {
        return invalid_parameter(
            field,
            "out_of_range_or_non_finite",
            Json{{"minimum", minimum}, {"maximum", maximum}});
    }
    output = value;
    return std::nullopt;
}

std::optional<BackendError> read_boolean(
    const Json& object,
    const char* key,
    const std::string& field,
    bool& output)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return std::nullopt;
    }
    if (!iterator->is_boolean()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "boolean"}});
    }
    output = iterator->get<bool>();
    return std::nullopt;
}

std::optional<BackendError> parse_crob(
    const Json& value, const std::string& prefix, CommandPoint& command)
{
    static constexpr std::array<std::string_view, 9> fields{
        "type",
        "index",
        "operation",
        "trip_close",
        "clear",
        "count",
        "on_time_ms",
        "off_time_ms",
        "value"};
    if (const auto error = reject_unknown_fields(value, fields, prefix)) {
        return error;
    }
    if (value.find("value") != value.end()) {
        return invalid_parameter(prefix + ".value", "field_not_allowed_for_crob");
    }

    std::string operation;
    if (const auto error = read_string(
            value, "operation", prefix + ".operation", operation, true, 1, 32)) {
        return error;
    }
    if (operation == "null") {
        command.crob_operation = CrobOperation::Null;
    }
    else if (operation == "pulse_on") {
        command.crob_operation = CrobOperation::PulseOn;
    }
    else if (operation == "pulse_off") {
        command.crob_operation = CrobOperation::PulseOff;
    }
    else if (operation == "latch_on") {
        command.crob_operation = CrobOperation::LatchOn;
    }
    else if (operation == "latch_off") {
        command.crob_operation = CrobOperation::LatchOff;
    }
    else {
        return invalid_parameter(prefix + ".operation", "unknown_enum");
    }

    std::string trip_close{"null"};
    if (const auto error = read_string(
            value, "trip_close", prefix + ".trip_close", trip_close, false, 1, 16)) {
        return error;
    }
    if (trip_close == "null") {
        command.trip_close = TripCloseSelection::Null;
    }
    else if (trip_close == "close") {
        command.trip_close = TripCloseSelection::Close;
    }
    else if (trip_close == "trip") {
        command.trip_close = TripCloseSelection::Trip;
    }
    else {
        return invalid_parameter(prefix + ".trip_close", "unknown_enum");
    }

    if (const auto error = read_boolean(
            value, "clear", prefix + ".clear", command.clear)) {
        return error;
    }
    std::uint64_t count = command.count;
    std::uint64_t on_time = command.on_time_ms;
    std::uint64_t off_time = command.off_time_ms;
    if (const auto error = read_unsigned(
            value, "count", prefix + ".count", 1, 255, count, false)) {
        return error;
    }
    if (const auto error = read_unsigned(
            value,
            "on_time_ms",
            prefix + ".on_time_ms",
            0,
            std::numeric_limits<std::uint32_t>::max(),
            on_time,
            false)) {
        return error;
    }
    if (const auto error = read_unsigned(
            value,
            "off_time_ms",
            prefix + ".off_time_ms",
            0,
            std::numeric_limits<std::uint32_t>::max(),
            off_time,
            false)) {
        return error;
    }
    command.count = static_cast<std::uint8_t>(count);
    command.on_time_ms = static_cast<std::uint32_t>(on_time);
    command.off_time_ms = static_cast<std::uint32_t>(off_time);
    return std::nullopt;
}

std::optional<BackendError> parse_analog(
    const Json& value,
    const std::string& prefix,
    const CommandKind kind,
    CommandPoint& command)
{
    static constexpr std::array<std::string_view, 3> fields{"type", "index", "value"};
    if (const auto error = reject_unknown_fields(value, fields, prefix)) {
        return error;
    }
    command.kind = kind;
    if (kind == CommandKind::AnalogOutputInt16) {
        return read_signed(
            value,
            "value",
            prefix + ".value",
            std::numeric_limits<std::int16_t>::min(),
            std::numeric_limits<std::int16_t>::max(),
            command.integer_value);
    }
    if (kind == CommandKind::AnalogOutputInt32) {
        return read_signed(
            value,
            "value",
            prefix + ".value",
            std::numeric_limits<std::int32_t>::min(),
            std::numeric_limits<std::int32_t>::max(),
            command.integer_value);
    }
    if (kind == CommandKind::AnalogOutputFloat32) {
        return read_floating(
            value,
            "value",
            prefix + ".value",
            -static_cast<double>(std::numeric_limits<float>::max()),
            static_cast<double>(std::numeric_limits<float>::max()),
            command.floating_value);
    }
    return read_floating(
        value,
        "value",
        prefix + ".value",
        -std::numeric_limits<double>::max(),
        std::numeric_limits<double>::max(),
        command.floating_value);
}

std::string command_identity(const CommandPoint& command)
{
    return std::to_string(static_cast<int>(command.kind)) + ":"
        + std::to_string(command.index);
}

}  // namespace

std::optional<BackendError> parse_command_config(
    const Json& params, CommandConfig& output)
{
    static constexpr std::array<std::string_view, 4> fields{
        "commands", "timeout_ms", "safety_token", "response_mode"};
    if (const auto error = reject_unknown_fields(params, fields, "params")) {
        return error;
    }

    if (const auto error = read_string(
            params,
            "safety_token",
            "safety_token",
            output.safety_token,
            true,
            32,
            32)) {
        return error;
    }
    for (const auto character : output.safety_token) {
        const auto hexadecimal = (character >= '0' && character <= '9')
            || (character >= 'a' && character <= 'f')
            || (character >= 'A' && character <= 'F');
        if (!hexadecimal) {
            return invalid_parameter("safety_token", "invalid_hex_token");
        }
    }

    std::uint64_t timeout = output.timeout_ms;
    if (const auto error = read_unsigned(
            params, "timeout_ms", "timeout_ms", 50, 300000, timeout, false)) {
        return error;
    }
    output.timeout_ms = static_cast<std::uint32_t>(timeout);

    std::string response_mode{"response"};
    if (const auto error = read_string(
            params,
            "response_mode",
            "response_mode",
            response_mode,
            false,
            1,
            32)) {
        return error;
    }
    if (response_mode == "response") {
        output.no_response = false;
    }
    else if (response_mode == "no_response") {
        output.no_response = true;
    }
    else {
        return invalid_parameter("response_mode", "unknown_enum");
    }

    const auto commands = params.find("commands");
    if (commands == params.end()) {
        return invalid_parameter("commands", "missing_field");
    }
    if (!commands->is_array()) {
        return invalid_parameter("commands", "invalid_type", Json{{"expected", "array"}});
    }
    if (commands->empty() || commands->size() > 256) {
        return invalid_parameter(
            "commands",
            "invalid_length",
            Json{{"minimum", 1}, {"maximum", 256}, {"received", commands->size()}});
    }

    output.commands.clear();
    output.commands.reserve(commands->size());
    std::set<std::string> identities;
    for (std::size_t ordinal = 0; ordinal < commands->size(); ++ordinal) {
        const auto& value = (*commands)[ordinal];
        const auto prefix = "commands[" + std::to_string(ordinal) + "]";
        if (!value.is_object()) {
            return invalid_parameter(prefix, "invalid_type", Json{{"expected", "object"}});
        }
        std::string type;
        if (const auto error = read_string(
                value, "type", prefix + ".type", type, true, 1, 64)) {
            return error;
        }
        CommandPoint command;
        std::uint64_t index = 0;
        if (const auto error = read_unsigned(
                value, "index", prefix + ".index", 0, 65535, index, true)) {
            return error;
        }
        command.index = static_cast<std::uint16_t>(index);

        std::optional<BackendError> error;
        if (type == "crob") {
            command.kind = CommandKind::Crob;
            error = parse_crob(value, prefix, command);
        }
        else if (type == "analog_output_int16") {
            error = parse_analog(value, prefix, CommandKind::AnalogOutputInt16, command);
        }
        else if (type == "analog_output_int32") {
            error = parse_analog(value, prefix, CommandKind::AnalogOutputInt32, command);
        }
        else if (type == "analog_output_float32") {
            error = parse_analog(value, prefix, CommandKind::AnalogOutputFloat32, command);
        }
        else if (type == "analog_output_double64") {
            error = parse_analog(value, prefix, CommandKind::AnalogOutputDouble64, command);
        }
        else {
            return invalid_parameter(prefix + ".type", "unknown_enum");
        }
        if (error) {
            return error;
        }
        if (!identities.insert(command_identity(command)).second) {
            return invalid_parameter(
                prefix,
                "duplicate_command_point",
                Json{{"type", type}, {"index", command.index}});
        }
        output.commands.push_back(command);
    }
    return std::nullopt;
}

}  // namespace dnp3host
