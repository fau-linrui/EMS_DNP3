#include "dnp3host/CaptureConfig.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <limits>
#include <set>
#include <string>
#include <string_view>
#include <utility>

namespace dnp3host {
namespace {

constexpr std::uint64_t kMaximumExpectedPoints = 1000000;
constexpr std::size_t kMaximumRanges = 256;
constexpr std::size_t kMaximumSources = 2;
constexpr std::size_t kMaximumQueueCapacity = 65536;
constexpr std::size_t kMaximumMismatchSamples = 1024;
constexpr std::uint32_t kMaximumDurationMs = 7U * 24U * 60U * 60U * 1000U;

BackendError invalid_parameter(
    const std::string& field,
    const std::string& reason,
    Json details = Json::object())
{
    details["reason"] = reason;
    details["field"] = field;
    return BackendError{
        ErrorCode::InvalidRequest, "invalid capture parameter", std::move(details)};
}

template <std::size_t Size>
std::optional<BackendError> reject_unknown_fields(
    const Json& value,
    const std::array<std::string_view, Size>& allowed,
    const std::string& prefix)
{
    if (!value.is_object()) {
        return invalid_parameter(prefix, "invalid_type", Json{{"expected", "object"}});
    }
    for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
        if (std::find(allowed.begin(), allowed.end(), iterator.key()) == allowed.end()) {
            return invalid_parameter(
                prefix.empty() ? iterator.key() : prefix + "." + iterator.key(),
                "unknown_field");
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
        return required ? std::optional<BackendError>{invalid_parameter(field, "missing_field")}
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

bool is_printable_ascii_token(const std::string& value, const std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) {
        return false;
    }
    return std::all_of(value.begin(), value.end(), [](const char character) {
        const auto byte = static_cast<unsigned char>(character);
        return byte >= 0x21U && byte <= 0x7EU && std::isspace(byte) == 0;
    });
}

std::optional<BackendError> read_token(
    const Json& object,
    const char* key,
    const std::string& field,
    std::string& output,
    const std::size_t maximum,
    const bool required = true)
{
    const auto iterator = object.find(key);
    if (iterator == object.end()) {
        return required ? std::optional<BackendError>{invalid_parameter(field, "missing_field")}
                        : std::nullopt;
    }
    if (!iterator->is_string()) {
        return invalid_parameter(field, "invalid_type", Json{{"expected", "string"}});
    }
    const auto value = iterator->get<std::string>();
    if (!is_printable_ascii_token(value, maximum)) {
        return invalid_parameter(
            field,
            "invalid_token",
            Json{{"minimum_bytes", 1}, {"maximum_bytes", maximum}});
    }
    output = value;
    return std::nullopt;
}

bool is_known_kind(const std::string& kind)
{
    static constexpr std::array<std::string_view, 11> kinds{
        "analog_command_event",
        "analog_input",
        "analog_output_status",
        "binary_command_event",
        "binary_input",
        "binary_output_status",
        "counter",
        "double_bit_binary_input",
        "frozen_counter",
        "octet_string",
        "time_and_interval"};
    return std::find(kinds.begin(), kinds.end(), kind) != kinds.end();
}

std::optional<BackendError> parse_sources(
    const Json& params, std::vector<std::string>& output)
{
    const auto iterator = params.find("sources");
    if (iterator == params.end()) {
        return invalid_parameter("sources", "missing_field");
    }
    if (!iterator->is_array()) {
        return invalid_parameter("sources", "invalid_type", Json{{"expected", "array"}});
    }
    if (iterator->empty() || iterator->size() > kMaximumSources) {
        return invalid_parameter(
            "sources",
            "invalid_length",
            Json{{"minimum_items", 1}, {"maximum_items", kMaximumSources}});
    }
    std::set<std::string> seen;
    output.clear();
    for (std::size_t index = 0; index < iterator->size(); ++index) {
        const auto& item = (*iterator)[index];
        if (!item.is_string()) {
            return invalid_parameter(
                "sources[" + std::to_string(index) + "]",
                "invalid_type",
                Json{{"expected", "string"}});
        }
        const auto source = item.get<std::string>();
        if (source != "solicited" && source != "unsolicited") {
            return invalid_parameter(
                "sources[" + std::to_string(index) + "]",
                "invalid_enum",
                Json{{"allowed", Json::array({"solicited", "unsolicited"})},
                     {"received", source}});
        }
        if (!seen.insert(source).second) {
            return invalid_parameter(
                "sources[" + std::to_string(index) + "]",
                "duplicate_value",
                Json{{"received", source}});
        }
        output.push_back(source);
    }
    return std::nullopt;
}

std::optional<BackendError> parse_point_ranges(
    const Json& expected, std::vector<CapturePointRange>& output)
{
    static constexpr std::array<std::string_view, 1> fields{"point_ranges"};
    static constexpr std::array<std::string_view, 3> range_fields{"kind", "start", "stop"};
    if (const auto error = reject_unknown_fields(expected, fields, "expected")) {
        return error;
    }
    const auto ranges = expected.find("point_ranges");
    if (ranges == expected.end()) {
        return invalid_parameter("expected.point_ranges", "missing_field");
    }
    if (!ranges->is_array()) {
        return invalid_parameter(
            "expected.point_ranges", "invalid_type", Json{{"expected", "array"}});
    }
    if (ranges->empty() || ranges->size() > kMaximumRanges) {
        return invalid_parameter(
            "expected.point_ranges",
            "invalid_length",
            Json{{"minimum_items", 1}, {"maximum_items", kMaximumRanges}});
    }

    std::uint64_t expected_total = 0;
    output.clear();
    output.reserve(ranges->size());
    for (std::size_t index = 0; index < ranges->size(); ++index) {
        const auto prefix = "expected.point_ranges[" + std::to_string(index) + "]";
        const auto& item = (*ranges)[index];
        if (const auto error = reject_unknown_fields(item, range_fields, prefix)) {
            return error;
        }
        CapturePointRange range;
        if (const auto error = read_token(item, "kind", prefix + ".kind", range.kind, 64)) {
            return error;
        }
        if (!is_known_kind(range.kind)) {
            return invalid_parameter(
                prefix + ".kind", "invalid_enum", Json{{"received", range.kind}});
        }
        std::uint64_t start = 0;
        std::uint64_t stop = 0;
        if (const auto error = read_unsigned(
                item, "start", prefix + ".start", 0, 65535, start, true)) {
            return error;
        }
        if (const auto error = read_unsigned(
                item, "stop", prefix + ".stop", 0, 65535, stop, true)) {
            return error;
        }
        if (start > stop) {
            return invalid_parameter(
                prefix, "invalid_range", Json{{"start", start}, {"stop", stop}});
        }
        range.start = static_cast<std::uint16_t>(start);
        range.stop = static_cast<std::uint16_t>(stop);
        const auto range_size = stop - start + 1U;
        if (expected_total > kMaximumExpectedPoints - range_size) {
            return invalid_parameter(
                "expected.point_ranges",
                "expected_set_too_large",
                Json{{"maximum_points", kMaximumExpectedPoints}});
        }
        expected_total += range_size;
        output.push_back(std::move(range));
    }

    std::sort(output.begin(), output.end(), [](const auto& left, const auto& right) {
        return left.kind < right.kind
            || (left.kind == right.kind && left.start < right.start);
    });
    for (std::size_t index = 1; index < output.size(); ++index) {
        const auto& previous = output[index - 1];
        const auto& current = output[index];
        if (previous.kind == current.kind && current.start <= previous.stop) {
            return invalid_parameter(
                "expected.point_ranges",
                "overlapping_ranges",
                Json{{"kind", current.kind},
                     {"previous_start", previous.start},
                     {"previous_stop", previous.stop},
                     {"current_start", current.start},
                     {"current_stop", current.stop}});
        }
    }
    return std::nullopt;
}

bool is_sha256(const std::string& value)
{
    return value.size() == 64U
        && std::all_of(value.begin(), value.end(), [](const char character) {
               const auto byte = static_cast<unsigned char>(character);
               return std::isxdigit(byte) != 0;
           });
}

std::optional<BackendError> parse_event_manifest(
    const Json& expected, CaptureEventManifest& output)
{
    static constexpr std::array<std::string_view, 1> expected_fields{"manifest"};
    static constexpr std::array<std::string_view, 9> manifest_fields{
        "generator",
        "generator_version",
        "scenario_id",
        "seed",
        "start_sequence",
        "end_sequence",
        "event_total",
        "sha256",
        "match_rule"};
    if (const auto error = reject_unknown_fields(expected, expected_fields, "expected")) {
        return error;
    }
    const auto manifest = expected.find("manifest");
    if (manifest == expected.end()) {
        return invalid_parameter("expected.manifest", "missing_field");
    }
    if (const auto error = reject_unknown_fields(
            *manifest, manifest_fields, "expected.manifest")) {
        return error;
    }
    for (const auto field : {"generator", "generator_version", "scenario_id"}) {
        std::string* target = field == std::string_view{"generator"}
            ? &output.generator
            : field == std::string_view{"generator_version"}
            ? &output.generator_version
            : &output.scenario_id;
        if (const auto error = read_token(
                *manifest,
                field,
                std::string{"expected.manifest."} + field,
                *target,
                128)) {
            return error;
        }
    }
    constexpr auto maximum = static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max());
    if (const auto error = read_unsigned(
            *manifest, "seed", "expected.manifest.seed", 0, maximum, output.seed, true)) {
        return error;
    }
    if (const auto error = read_unsigned(
            *manifest,
            "start_sequence",
            "expected.manifest.start_sequence",
            0,
            maximum,
            output.start_sequence,
            true)) {
        return error;
    }
    if (const auto error = read_unsigned(
            *manifest,
            "end_sequence",
            "expected.manifest.end_sequence",
            0,
            maximum,
            output.end_sequence,
            true)) {
        return error;
    }
    if (const auto error = read_unsigned(
            *manifest,
            "event_total",
            "expected.manifest.event_total",
            1,
            1000000000,
            output.event_total,
            true)) {
        return error;
    }
    if (output.start_sequence > output.end_sequence
        || output.end_sequence - output.start_sequence + 1U != output.event_total) {
        return invalid_parameter(
            "expected.manifest",
            "inconsistent_sequence_range",
            Json{{"start_sequence", output.start_sequence},
                 {"end_sequence", output.end_sequence},
                 {"event_total", output.event_total}});
    }
    if (const auto error = read_token(
            *manifest, "sha256", "expected.manifest.sha256", output.sha256, 64)) {
        return error;
    }
    if (!is_sha256(output.sha256)) {
        return invalid_parameter("expected.manifest.sha256", "invalid_sha256");
    }
    std::transform(
        output.sha256.begin(), output.sha256.end(), output.sha256.begin(), [](const char value) {
            return static_cast<char>(std::tolower(static_cast<unsigned char>(value)));
        });
    if (const auto error = read_token(
            *manifest,
            "match_rule",
            "expected.manifest.match_rule",
            output.match_rule,
            64)) {
        return error;
    }
    if (output.match_rule != "ordered_kind_index_value") {
        return invalid_parameter(
            "expected.manifest.match_rule",
            "invalid_enum",
            Json{{"allowed", Json::array({"ordered_kind_index_value"})},
                 {"received", output.match_rule}});
    }
    return std::nullopt;
}

}  // namespace

std::optional<BackendError> parse_capture_config(
    const Json& params, CaptureConfig& output)
{
    static constexpr std::array<std::string_view, 6> fields{
        "mode",
        "sources",
        "expected",
        "mismatch_sample_limit",
        "queue_capacity",
        "duration_limit_ms"};
    if (const auto error = reject_unknown_fields(params, fields, "")) {
        return error;
    }
    const auto mode = params.find("mode");
    if (mode == params.end()) {
        return invalid_parameter("mode", "missing_field");
    }
    if (!mode->is_string()) {
        return invalid_parameter("mode", "invalid_type", Json{{"expected", "string"}});
    }
    const auto mode_name = mode->get<std::string>();
    if (mode_name == "static_set") {
        output.mode = CaptureMode::StaticSet;
    }
    else if (mode_name == "event_sequence") {
        output.mode = CaptureMode::EventSequence;
    }
    else if (mode_name == "observation") {
        output.mode = CaptureMode::Observation;
    }
    else {
        return invalid_parameter(
            "mode",
            "invalid_enum",
            Json{{"allowed", Json::array({"static_set", "event_sequence", "observation"})},
                 {"received", mode_name}});
    }

    if (const auto error = parse_sources(params, output.sources)) {
        return error;
    }
    std::uint64_t sample_limit = output.mismatch_sample_limit;
    std::uint64_t queue_capacity = output.queue_capacity;
    std::uint64_t duration_limit = output.duration_limit_ms;
    if (const auto error = read_unsigned(
            params,
            "mismatch_sample_limit",
            "mismatch_sample_limit",
            0,
            kMaximumMismatchSamples,
            sample_limit)) {
        return error;
    }
    if (const auto error = read_unsigned(
            params,
            "queue_capacity",
            "queue_capacity",
            1,
            kMaximumQueueCapacity,
            queue_capacity)) {
        return error;
    }
    if (const auto error = read_unsigned(
            params,
            "duration_limit_ms",
            "duration_limit_ms",
            100,
            kMaximumDurationMs,
            duration_limit,
            true)) {
        return error;
    }
    output.mismatch_sample_limit = static_cast<std::size_t>(sample_limit);
    output.queue_capacity = static_cast<std::size_t>(queue_capacity);
    output.duration_limit_ms = static_cast<std::uint32_t>(duration_limit);

    const auto expected = params.find("expected");
    if (output.mode == CaptureMode::Observation) {
        if (expected != params.end()) {
            return invalid_parameter("expected", "unexpected_for_mode", Json{{"mode", mode_name}});
        }
        output.point_ranges.clear();
        output.event_manifest.reset();
        return std::nullopt;
    }
    if (expected == params.end()) {
        return invalid_parameter("expected", "missing_field", Json{{"mode", mode_name}});
    }
    if (!expected->is_object()) {
        return invalid_parameter("expected", "invalid_type", Json{{"expected", "object"}});
    }
    if (output.mode == CaptureMode::StaticSet) {
        output.event_manifest.reset();
        return parse_point_ranges(*expected, output.point_ranges);
    }
    output.point_ranges.clear();
    CaptureEventManifest manifest;
    if (const auto error = parse_event_manifest(*expected, manifest)) {
        return error;
    }
    output.event_manifest = std::move(manifest);
    return std::nullopt;
}

std::optional<BackendError> parse_capture_reference_config(
    const Json& params,
    const bool accept_drain_timeout,
    CaptureReferenceConfig& output)
{
    static constexpr std::array<std::string_view, 1> progress_fields{"capture_id"};
    static constexpr std::array<std::string_view, 2> end_fields{"capture_id", "drain_timeout_ms"};
    const auto error = accept_drain_timeout
        ? reject_unknown_fields(params, end_fields, "")
        : reject_unknown_fields(params, progress_fields, "");
    if (error) {
        return error;
    }
    if (const auto token_error = read_token(
            params, "capture_id", "capture_id", output.capture_id, 64)) {
        return token_error;
    }
    if (output.capture_id.find_first_not_of(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")
        != std::string::npos) {
        return invalid_parameter("capture_id", "invalid_token");
    }
    if (!accept_drain_timeout) {
        return std::nullopt;
    }
    std::uint64_t drain_timeout = output.drain_timeout_ms;
    if (const auto drain_error = read_unsigned(
            params,
            "drain_timeout_ms",
            "drain_timeout_ms",
            50,
            300000,
            drain_timeout)) {
        return drain_error;
    }
    output.drain_timeout_ms = static_cast<std::uint32_t>(drain_timeout);
    return std::nullopt;
}

}  // namespace dnp3host
