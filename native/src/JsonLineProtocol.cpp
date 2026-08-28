#include "dnp3host/JsonLineProtocol.h"

#include "dnp3host/ProjectInfo.h"

#include <cmath>
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace dnp3host {
namespace {

ProtocolError invalid_request(
    std::optional<std::string> id,
    const std::string& message,
    const std::string& reason)
{
    return ProtocolError{
        std::move(id), ErrorCode::InvalidRequest, message, Json{{"reason", reason}}};
}

bool is_valid_utf8(const std::string_view input) noexcept
{
    std::size_t index = 0;
    while (index < input.size()) {
        const auto first = static_cast<std::uint8_t>(input[index]);
        if (first <= 0x7F) {
            ++index;
            continue;
        }

        std::size_t continuation_count = 0;
        std::uint32_t code_point = 0;
        std::uint32_t minimum = 0;
        if ((first & 0xE0U) == 0xC0U) {
            continuation_count = 1;
            code_point = first & 0x1FU;
            minimum = 0x80;
        }
        else if ((first & 0xF0U) == 0xE0U) {
            continuation_count = 2;
            code_point = first & 0x0FU;
            minimum = 0x800;
        }
        else if ((first & 0xF8U) == 0xF0U) {
            continuation_count = 3;
            code_point = first & 0x07U;
            minimum = 0x10000;
        }
        else {
            return false;
        }

        if (index + continuation_count >= input.size()) {
            return false;
        }
        for (std::size_t offset = 1; offset <= continuation_count; ++offset) {
            const auto next = static_cast<std::uint8_t>(input[index + offset]);
            if ((next & 0xC0U) != 0x80U) {
                return false;
            }
            code_point = (code_point << 6U) | (next & 0x3FU);
        }

        if (code_point < minimum || code_point > 0x10FFFFU
            || (code_point >= 0xD800U && code_point <= 0xDFFFU)) {
            return false;
        }
        index += continuation_count + 1;
    }
    return true;
}

bool exceeds_json_depth(const std::string_view input, const std::size_t maximum) noexcept
{
    bool in_string = false;
    bool escaped = false;
    std::size_t depth = 0;
    for (const char character : input) {
        if (in_string) {
            if (escaped) {
                escaped = false;
            }
            else if (character == '\\') {
                escaped = true;
            }
            else if (character == '"') {
                in_string = false;
            }
            continue;
        }

        if (character == '"') {
            in_string = true;
        }
        else if (character == '{' || character == '[') {
            ++depth;
            if (depth > maximum) {
                return true;
            }
        }
        else if ((character == '}' || character == ']') && depth > 0) {
            --depth;
        }
    }
    return false;
}

bool contains_non_finite_number(const Json& value)
{
    if (value.is_number_float()) {
        return !std::isfinite(value.get<double>());
    }
    if (value.is_array()) {
        for (const auto& item : value) {
            if (contains_non_finite_number(item)) {
                return true;
            }
        }
    }
    else if (value.is_object()) {
        for (const auto& item : value.items()) {
            if (contains_non_finite_number(item.value())) {
                return true;
            }
        }
    }
    return false;
}

bool is_valid_ascii_token(
    const std::string& value,
    const std::size_t maximum,
    const bool allow_colon) noexcept
{
    if (value.empty() || value.size() > maximum) {
        return false;
    }
    for (const unsigned char character : value) {
        const bool alphanumeric = (character >= 'a' && character <= 'z')
            || (character >= 'A' && character <= 'Z')
            || (character >= '0' && character <= '9');
        if (!alphanumeric && character != '.' && character != '_' && character != '-'
            && !(allow_colon && character == ':')) {
            return false;
        }
    }
    return true;
}

struct DuplicateKeyState {
    bool duplicate_found{false};
    std::vector<std::unordered_set<std::string>> object_keys;
};

}  // namespace

ReadLineResult JsonLineProtocol::read_line(
    std::istream& input,
    const std::size_t max_request_bytes)
{
    ReadLineResult result;
    result.status = ReadLineStatus::EndOfFile;
    result.line.reserve(max_request_bytes < 4096 ? max_request_bytes + 1 : 4096);

    bool received_anything = false;
    bool exceeded_limit = false;
    while (true) {
        const int next = input.get();
        if (next == std::char_traits<char>::eof()) {
            if (input.bad()) {
                return ReadLineResult{ReadLineStatus::IoError, {}};
            }
            if (!received_anything) {
                return ReadLineResult{ReadLineStatus::EndOfFile, {}};
            }
            break;
        }

        received_anything = true;
        if (next == '\n') {
            break;
        }

        if (!exceeded_limit) {
            if (result.line.size() <= max_request_bytes) {
                result.line.push_back(static_cast<char>(next));
            }
            else {
                exceeded_limit = true;
                result.line.clear();
            }
        }
    }

    if (exceeded_limit) {
        return ReadLineResult{ReadLineStatus::TooLarge, {}};
    }
    if (result.line.size() > max_request_bytes) {
        if (result.line.size() == max_request_bytes + 1 && result.line.back() == '\r') {
            result.line.pop_back();
        }
        else {
            return ReadLineResult{ReadLineStatus::TooLarge, {}};
        }
    }
    else if (!result.line.empty() && result.line.back() == '\r') {
        result.line.pop_back();
    }

    result.status = ReadLineStatus::Line;
    return result;
}

ParseResult JsonLineProtocol::parse_request(const std::string_view line)
{
    if (line.empty()) {
        return ParseResult{
            std::nullopt,
            invalid_request(std::nullopt, "request line must not be empty", "empty_line")};
    }
    if (line.size() >= 3 && static_cast<std::uint8_t>(line[0]) == 0xEF
        && static_cast<std::uint8_t>(line[1]) == 0xBB
        && static_cast<std::uint8_t>(line[2]) == 0xBF) {
        return ParseResult{
            std::nullopt,
            invalid_request(
                std::nullopt, "UTF-8 byte order marks are not allowed", "utf8_bom")};
    }
    if (!is_valid_utf8(line)) {
        return ParseResult{
            std::nullopt,
            invalid_request(std::nullopt, "request is not valid UTF-8", "invalid_utf8")};
    }
    if (exceeds_json_depth(line, kMaxJsonDepth)) {
        return ParseResult{
            std::nullopt,
            invalid_request(
                std::nullopt, "JSON nesting exceeds the configured limit", "json_too_deep")};
    }

    const auto duplicate_state = std::make_shared<DuplicateKeyState>();
    const Json::parser_callback_t callback =
        [duplicate_state](int, const Json::parse_event_t event, Json& parsed) {
            if (event == Json::parse_event_t::object_start) {
                duplicate_state->object_keys.emplace_back();
            }
            else if (event == Json::parse_event_t::key) {
                if (duplicate_state->object_keys.empty()) {
                    duplicate_state->duplicate_found = true;
                }
                else {
                    const auto& key = parsed.get_ref<const std::string&>();
                    const auto inserted = duplicate_state->object_keys.back().insert(key);
                    if (!inserted.second) {
                        duplicate_state->duplicate_found = true;
                    }
                }
            }
            else if (event == Json::parse_event_t::object_end
                     && !duplicate_state->object_keys.empty()) {
                duplicate_state->object_keys.pop_back();
            }
            return true;
        };

    Json parsed;
    try {
        parsed = Json::parse(line.begin(), line.end(), callback, true, false);
    }
    catch (const std::exception&) {
        return ParseResult{
            std::nullopt,
            invalid_request(std::nullopt, "request is not valid JSON", "invalid_json")};
    }

    if (duplicate_state->duplicate_found) {
        return ParseResult{
            std::nullopt,
            invalid_request(
                std::nullopt, "duplicate JSON object keys are not allowed", "duplicate_key")};
    }
    if (contains_non_finite_number(parsed)) {
        return ParseResult{
            std::nullopt,
            invalid_request(
                std::nullopt, "non-finite JSON numbers are not allowed", "non_finite_number")};
    }
    if (!parsed.is_object()) {
        return ParseResult{
            std::nullopt,
            invalid_request(std::nullopt, "request root must be an object", "root_not_object")};
    }

    std::optional<std::string> candidate_id;
    const auto id_iterator = parsed.find("id");
    if (id_iterator != parsed.end() && id_iterator->is_string()) {
        const auto id = id_iterator->get<std::string>();
        if (is_valid_ascii_token(id, kMaxRequestIdBytes, true)) {
            candidate_id = id;
        }
    }

    static const std::unordered_set<std::string> allowed_fields{
        "schema_version", "id", "cmd", "params"};
    for (const auto& item : parsed.items()) {
        if (allowed_fields.find(item.key()) == allowed_fields.end()) {
            auto error = invalid_request(
                candidate_id, "request contains an unknown top-level field", "unknown_field");
            error.details["field"] = item.key();
            return ParseResult{std::nullopt, std::move(error)};
        }
    }
    for (const auto* required : {"schema_version", "id", "cmd", "params"}) {
        if (!parsed.contains(required)) {
            auto error = invalid_request(
                candidate_id, "request is missing a required field", "missing_field");
            error.details["field"] = required;
            return ParseResult{std::nullopt, std::move(error)};
        }
    }

    if (!parsed["schema_version"].is_number_integer()
        && !parsed["schema_version"].is_number_unsigned()) {
        return ParseResult{
            std::nullopt,
            invalid_request(
                candidate_id, "schema_version must be an integer", "invalid_schema_type")};
    }
    if (parsed["schema_version"] != kSchemaVersion) {
        return ParseResult{
            std::nullopt,
            ProtocolError{
                candidate_id,
                ErrorCode::SchemaMismatch,
                "request schema version is not supported",
                Json{{"expected", kSchemaVersion}, {"received", parsed["schema_version"]}}}};
    }
    if (!parsed["id"].is_string()
        || !is_valid_ascii_token(
            parsed["id"].is_string() ? parsed["id"].get_ref<const std::string&>() : "",
            kMaxRequestIdBytes,
            true)) {
        return ParseResult{
            std::nullopt,
            invalid_request(std::nullopt, "request id is invalid", "invalid_id")};
    }
    if (!parsed["cmd"].is_string()
        || !is_valid_ascii_token(
            parsed["cmd"].is_string() ? parsed["cmd"].get_ref<const std::string&>() : "",
            kMaxCommandBytes,
            false)) {
        return ParseResult{
            std::nullopt,
            invalid_request(candidate_id, "request command is invalid", "invalid_command")};
    }
    if (!parsed["params"].is_object()) {
        return ParseResult{
            std::nullopt,
            invalid_request(candidate_id, "params must be an object", "invalid_params")};
    }

    return ParseResult{
        Request{
            parsed["id"].get<std::string>(),
            parsed["cmd"].get<std::string>(),
            std::move(parsed["params"])},
        std::nullopt};
}

Json JsonLineProtocol::success_response(const std::string_view id, Json result)
{
    return Json{
        {"schema_version", kSchemaVersion},
        {"id", id},
        {"ok", true},
        {"result", std::move(result)}};
}

Json JsonLineProtocol::error_response(const ProtocolError& error)
{
    Json id = nullptr;
    if (error.id) {
        id = *error.id;
    }
    return Json{
        {"schema_version", kSchemaVersion},
        {"id", std::move(id)},
        {"ok", false},
        {"error",
         Json{
             {"code", to_string(error.code)},
             {"message", error.message},
             {"details", error.details}}}};
}

std::string JsonLineProtocol::serialize(const Json& value)
{
    return value.dump(-1, ' ', false, Json::error_handler_t::strict);
}

}  // namespace dnp3host

