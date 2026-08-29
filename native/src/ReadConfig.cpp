#include "dnp3host/ReadConfig.h"

#include <array>
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

std::optional<BackendError> read_unsigned(
    const Json& object,
    const char* key,
    const std::string& field,
    const std::uint64_t minimum,
    const std::uint64_t maximum,
    std::uint64_t& output,
    const bool required = false)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        if (required) {
            return invalid_parameter(field, "missing_field");
        }
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
                Json{{"minimum", minimum},
                     {"maximum", maximum},
                     {"received", signed_value}});
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

std::optional<BackendError> parse_options(const Json& params, ReadOptions& output)
{
    std::uint64_t timeout = output.timeout_ms;
    std::uint64_t maximum_measurements = output.max_measurements;
    if (const auto error = read_unsigned(
            params, "timeout_ms", "timeout_ms", 50, 300000, timeout)) {
        return error;
    }
    if (const auto error = read_unsigned(
            params,
            "max_measurements",
            "max_measurements",
            1,
            1000000,
            maximum_measurements)) {
        return error;
    }

    const auto mode = params.find("return_mode");
    if (mode != params.end()) {
        if (!mode->is_string()) {
            return invalid_parameter(
                "return_mode", "invalid_type", Json{{"expected", "string"}});
        }
        const auto value = mode->get<std::string>();
        if (value == "detail") {
            output.return_mode = ReturnMode::Detail;
        }
        else if (value == "summary") {
            output.return_mode = ReturnMode::Summary;
        }
        else {
            return invalid_parameter(
                "return_mode",
                "invalid_enum",
                Json{{"allowed", Json::array({"detail", "summary"})},
                     {"received", value}});
        }
    }

    output.timeout_ms = static_cast<std::uint32_t>(timeout);
    output.max_measurements = static_cast<std::size_t>(maximum_measurements);
    return std::nullopt;
}

bool has_field(const Json& object, const char* key)
{
    return object.find(key) != object.end();
}

std::optional<BackendError> reject_field_for_qualifier(
    const Json& object,
    const char* key,
    const std::string& prefix,
    const std::string& qualifier)
{
    if (!has_field(object, key)) {
        return std::nullopt;
    }
    return invalid_parameter(
        prefix + "." + key,
        "unexpected_for_qualifier",
        Json{{"qualifier", qualifier}});
}

std::optional<BackendError> parse_header(
    const Json& value, const std::size_t index, ReadHeader& output)
{
    static constexpr std::array<std::string_view, 7> fields{
        "group", "variation", "qualifier", "start", "stop", "count", "reserved"};
    const auto prefix = "headers[" + std::to_string(index) + "]";
    if (!value.is_object()) {
        return invalid_parameter(prefix, "invalid_type", Json{{"expected", "object"}});
    }
    // "reserved" is never accepted semantically. Keeping it out of this list would
    // only report unknown_field; reject it explicitly below with a clearer reason.
    if (const auto error = reject_unknown_fields(value, fields, prefix)) {
        return error;
    }
    if (has_field(value, "reserved")) {
        return invalid_parameter(prefix + ".reserved", "reserved_field");
    }

    std::uint64_t group = 0;
    std::uint64_t variation = 0;
    if (const auto error = read_unsigned(
            value, "group", prefix + ".group", 0, 255, group, true)) {
        return error;
    }
    if (const auto error = read_unsigned(
            value, "variation", prefix + ".variation", 0, 255, variation, true)) {
        return error;
    }

    const auto qualifier_iterator = value.find("qualifier");
    if (qualifier_iterator == value.end()) {
        return invalid_parameter(prefix + ".qualifier", "missing_field");
    }
    if (!qualifier_iterator->is_string()) {
        return invalid_parameter(
            prefix + ".qualifier", "invalid_type", Json{{"expected", "string"}});
    }
    const auto qualifier = qualifier_iterator->get<std::string>();
    if (qualifier == "all_objects") {
        output.qualifier = ReadQualifier::AllObjects;
        for (const auto field : {"start", "stop", "count"}) {
            if (const auto error = reject_field_for_qualifier(
                    value, field, prefix, qualifier)) {
                return error;
            }
        }
    }
    else if (qualifier == "range8" || qualifier == "range16") {
        if (const auto error = reject_field_for_qualifier(
                value, "count", prefix, qualifier)) {
            return error;
        }
        const auto maximum = qualifier == "range8" ? 255U : 65535U;
        std::uint64_t start = 0;
        std::uint64_t stop = 0;
        if (const auto error = read_unsigned(
                value, "start", prefix + ".start", 0, maximum, start, true)) {
            return error;
        }
        if (const auto error = read_unsigned(
                value, "stop", prefix + ".stop", 0, maximum, stop, true)) {
            return error;
        }
        if (start > stop) {
            return invalid_parameter(
                prefix,
                "invalid_range",
                Json{{"start", start}, {"stop", stop}});
        }
        output.qualifier = qualifier == "range8" ? ReadQualifier::Range8
                                                   : ReadQualifier::Range16;
        output.start = static_cast<std::uint16_t>(start);
        output.stop = static_cast<std::uint16_t>(stop);
    }
    else if (qualifier == "count8" || qualifier == "count16") {
        for (const auto field : {"start", "stop"}) {
            if (const auto error = reject_field_for_qualifier(
                    value, field, prefix, qualifier)) {
                return error;
            }
        }
        const auto maximum = qualifier == "count8" ? 255U : 65535U;
        std::uint64_t count = 0;
        if (const auto error = read_unsigned(
                value, "count", prefix + ".count", 1, maximum, count, true)) {
            return error;
        }
        output.qualifier = qualifier == "count8" ? ReadQualifier::Count8
                                                   : ReadQualifier::Count16;
        output.count = static_cast<std::uint16_t>(count);
    }
    else {
        return invalid_parameter(
            prefix + ".qualifier",
            "invalid_enum",
            Json{{"allowed",
                  Json::array(
                      {"all_objects", "range8", "range16", "count8", "count16"})},
                 {"received", qualifier}});
    }

    if (group == 60 && variation >= 1 && variation <= 4
        && (output.qualifier == ReadQualifier::Range8
            || output.qualifier == ReadQualifier::Range16)) {
        return invalid_parameter(
            prefix + ".qualifier",
            "unsupported_combination",
            Json{{"group", group},
                 {"variation", variation},
                 {"reason_detail", "class data requests do not use range qualifiers"}});
    }

    output.group = static_cast<std::uint8_t>(group);
    output.variation = static_cast<std::uint8_t>(variation);
    return std::nullopt;
}

}  // namespace

std::optional<BackendError> parse_read_options(
    const Json& params, ReadOptions& output)
{
    static constexpr std::array<std::string_view, 3> fields{
        "timeout_ms", "max_measurements", "return_mode"};
    if (const auto error = reject_unknown_fields(params, fields, "")) {
        return error;
    }
    return parse_options(params, output);
}

std::optional<BackendError> parse_class_poll_config(
    const Json& params, ClassPollConfig& output)
{
    static constexpr std::array<std::string_view, 4> fields{
        "timeout_ms", "max_measurements", "return_mode", "classes"};
    if (const auto error = reject_unknown_fields(params, fields, "")) {
        return error;
    }
    if (const auto error = parse_options(params, output.options)) {
        return error;
    }

    const auto classes = params.find("classes");
    if (classes == params.end()) {
        return std::nullopt;
    }
    if (!classes->is_array()) {
        return invalid_parameter("classes", "invalid_type", Json{{"expected", "array"}});
    }
    if (classes->empty() || classes->size() > 3) {
        return invalid_parameter(
            "classes",
            "invalid_length",
            Json{{"minimum_items", 1},
                 {"maximum_items", 3},
                 {"received_items", classes->size()}});
    }

    std::uint8_t mask = 0;
    for (std::size_t index = 0; index < classes->size(); ++index) {
        const auto& item = (*classes)[index];
        if (!item.is_number_integer() && !item.is_number_unsigned()) {
            return invalid_parameter(
                "classes[" + std::to_string(index) + "]",
                "invalid_type",
                Json{{"expected", "integer"}});
        }
        const auto value = item.get<int>();
        if (value < 1 || value > 3) {
            return invalid_parameter(
                "classes[" + std::to_string(index) + "]",
                "out_of_range",
                Json{{"minimum", 1}, {"maximum", 3}, {"received", value}});
        }
        const auto bit = static_cast<std::uint8_t>(1U << value);
        if ((mask & bit) != 0U) {
            return invalid_parameter(
                "classes[" + std::to_string(index) + "]",
                "duplicate_value",
                Json{{"received", value}});
        }
        mask = static_cast<std::uint8_t>(mask | bit);
    }
    output.class_mask = mask;
    return std::nullopt;
}

std::optional<BackendError> parse_read_config(
    const Json& params, ReadConfig& output)
{
    static constexpr std::array<std::string_view, 4> fields{
        "timeout_ms", "max_measurements", "return_mode", "headers"};
    if (const auto error = reject_unknown_fields(params, fields, "")) {
        return error;
    }
    if (const auto error = parse_options(params, output.options)) {
        return error;
    }

    const auto headers = params.find("headers");
    if (headers == params.end()) {
        return invalid_parameter("headers", "missing_field");
    }
    if (!headers->is_array()) {
        return invalid_parameter("headers", "invalid_type", Json{{"expected", "array"}});
    }
    if (headers->empty() || headers->size() > 64) {
        return invalid_parameter(
            "headers",
            "invalid_length",
            Json{{"minimum_items", 1},
                 {"maximum_items", 64},
                 {"received_items", headers->size()}});
    }

    output.headers.clear();
    output.headers.reserve(headers->size());
    for (std::size_t index = 0; index < headers->size(); ++index) {
        ReadHeader header;
        if (const auto error = parse_header((*headers)[index], index, header)) {
            output.headers.clear();
            return error;
        }
        output.headers.push_back(header);
    }
    return std::nullopt;
}

}  // namespace dnp3host
