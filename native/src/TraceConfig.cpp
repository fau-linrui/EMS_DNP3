#include "dnp3host/TraceConfig.h"

#include <algorithm>
#include <array>
#include <string_view>

namespace dnp3host {
namespace {

BackendError invalid(const std::string& field, const char* reason)
{
    return BackendError{ErrorCode::InvalidRequest, "invalid protocol trace parameter",
                        Json{{"field", field}, {"reason", reason}}};
}

template <std::size_t Size>
std::optional<BackendError> reject_unknown(
    const Json& params, const std::array<std::string_view, Size>& allowed)
{
    if (!params.is_object()) {
        return invalid("params", "invalid_type");
    }
    for (auto item = params.begin(); item != params.end(); ++item) {
        if (std::find(allowed.begin(), allowed.end(), item.key()) == allowed.end()) {
            return invalid(item.key(), "unknown_field");
        }
    }
    return std::nullopt;
}

std::optional<BackendError> read_number(
    const Json& params, const char* key, const std::uint64_t minimum,
    const std::uint64_t maximum, std::uint64_t& value)
{
    const auto found = params.find(key);
    if (found == params.end()) {
        return std::nullopt;
    }
    if (!found->is_number_integer()) {
        return invalid(key, "invalid_type");
    }
    if (!found->is_number_unsigned() && found->get<std::int64_t>() < 0) {
        return invalid(key, "out_of_range");
    }
    const auto received = found->get<std::uint64_t>();
    if (received < minimum || received > maximum) {
        return invalid(key, "out_of_range");
    }
    value = received;
    return std::nullopt;
}

}  // namespace

std::optional<BackendError> parse_trace_start_config(
    const Json& params, TraceStartConfig& output)
{
    static constexpr std::array<std::string_view, 1> fields{"queue_capacity"};
    if (const auto error = reject_unknown(params, fields)) {
        return error;
    }
    std::uint64_t capacity = 16384;
    if (const auto error = read_number(params, "queue_capacity", 1, 65536, capacity)) {
        return error;
    }
    output = TraceStartConfig{static_cast<std::size_t>(capacity)};
    return std::nullopt;
}

std::optional<BackendError> parse_trace_reference_config(
    const Json& params, const bool accept_read_options, TraceReferenceConfig& output)
{
    static constexpr std::array<std::string_view, 3> read_fields{
        "trace_id", "max_records", "timeout_ms"};
    static constexpr std::array<std::string_view, 1> stop_fields{"trace_id"};
    if (const auto error = accept_read_options ? reject_unknown(params, read_fields)
                                              : reject_unknown(params, stop_fields)) {
        return error;
    }
    const auto id = params.find("trace_id");
    if (id == params.end()) {
        return invalid("trace_id", "missing_field");
    }
    if (!id->is_string()) {
        return invalid("trace_id", "invalid_type");
    }
    const auto text = id->get<std::string>();
    if (text.size() < 7 || text.size() > 64 || text.compare(0, 6, "trace-") != 0
        || text[6] < '1' || text[6] > '9'
        || !std::all_of(text.begin() + 6, text.end(), [](const char character) {
               return character >= '0' && character <= '9';
           })) {
        return invalid("trace_id", "invalid_token");
    }
    std::uint64_t max_records = 256;
    std::uint64_t timeout = 0;
    if (const auto error = read_number(params, "max_records", 1, 1024, max_records)) {
        return error;
    }
    if (const auto error = read_number(params, "timeout_ms", 0, 60000, timeout)) {
        return error;
    }
    output = TraceReferenceConfig{
        text, static_cast<std::size_t>(max_records), static_cast<std::uint32_t>(timeout)};
    return std::nullopt;
}

}  // namespace dnp3host
