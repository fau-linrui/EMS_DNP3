#pragma once

#include "dnp3host/Models.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace dnp3host {

struct ConnectionConfig {
    std::string host;
    std::uint16_t port{20000};
    std::string local_adapter{"0.0.0.0"};
    std::uint32_t connect_timeout_ms{5000};
    std::uint32_t retry_min_ms{1000};
    std::uint32_t retry_max_ms{60000};
    std::uint16_t master_address{1};
    std::uint16_t outstation_address{1024};
    std::uint32_t keep_alive_timeout_ms{60000};
    bool allow_state_change{false};
    std::string safety_environment{"UNSPECIFIED"};
    std::string operator_id;
    std::string dut_id;
};

struct WaitEventConfig {
    std::uint32_t timeout_ms{0};
    std::size_t max_events{64};
};

struct UnsolicitedControlConfig {
    std::uint32_t timeout_ms{5000};
    std::uint8_t class_mask{0x0E};
};

struct WaitUnsolicitedConfig {
    std::uint32_t timeout_ms{0};
    std::size_t max_events{256};
};

enum class CaptureMode {
    StaticSet,
    EventSequence,
    Observation,
};

struct CapturePointRange {
    std::string kind;
    std::uint16_t start{0};
    std::uint16_t stop{0};
};

struct CaptureEventManifest {
    std::string generator;
    std::string generator_version;
    std::string scenario_id;
    std::uint64_t seed{0};
    std::uint64_t start_sequence{0};
    std::uint64_t end_sequence{0};
    std::uint64_t event_total{0};
    std::string sha256;
    std::string match_rule;
};

struct CaptureConfig {
    CaptureMode mode{CaptureMode::Observation};
    std::vector<std::string> sources;
    std::vector<CapturePointRange> point_ranges;
    std::optional<CaptureEventManifest> event_manifest;
    std::size_t mismatch_sample_limit{100};
    std::size_t queue_capacity{4096};
    std::uint32_t duration_limit_ms{600000};
};

struct CaptureReferenceConfig {
    std::string capture_id;
    std::uint32_t drain_timeout_ms{5000};
};

enum class ReturnMode {
    Detail,
    Summary,
};

enum class ReadQualifier {
    AllObjects,
    Range8,
    Range16,
    Count8,
    Count16,
};

struct ReadOptions {
    std::uint32_t timeout_ms{5000};
    std::size_t max_measurements{10000};
    ReturnMode return_mode{ReturnMode::Detail};
};

struct ReadHeader {
    std::uint8_t group{0};
    std::uint8_t variation{0};
    ReadQualifier qualifier{ReadQualifier::AllObjects};
    std::uint16_t start{0};
    std::uint16_t stop{0};
    std::uint16_t count{0};
};

struct ClassPollConfig {
    ReadOptions options;
    std::uint8_t class_mask{0x0E};
};

struct ReadConfig {
    ReadOptions options;
    std::vector<ReadHeader> headers;
};

enum class CommandKind {
    Crob,
    AnalogOutputInt16,
    AnalogOutputInt32,
    AnalogOutputFloat32,
    AnalogOutputDouble64,
};

enum class CrobOperation {
    Null,
    PulseOn,
    PulseOff,
    LatchOn,
    LatchOff,
};

enum class TripCloseSelection {
    Null,
    Close,
    Trip,
};

struct CommandPoint {
    CommandKind kind{CommandKind::Crob};
    std::uint16_t index{0};
    CrobOperation crob_operation{CrobOperation::LatchOn};
    TripCloseSelection trip_close{TripCloseSelection::Null};
    bool clear{false};
    std::uint8_t count{1};
    std::uint32_t on_time_ms{100};
    std::uint32_t off_time_ms{100};
    std::int64_t integer_value{0};
    double floating_value{0.0};
};

struct CommandConfig {
    std::string safety_token;
    std::uint32_t timeout_ms{10000};
    bool no_response{false};
    std::vector<CommandPoint> commands;
};

struct BackendError {
    ErrorCode code{ErrorCode::InternalError};
    std::string message;
    Json details{Json::object()};
};

struct BackendOperationResult {
    Json result{Json::object()};
    std::optional<BackendError> error;

    static BackendOperationResult success(Json value)
    {
        return BackendOperationResult{std::move(value), std::nullopt};
    }

    static BackendOperationResult failure(
        const ErrorCode code, std::string message, Json details = Json::object())
    {
        return BackendOperationResult{
            Json::object(), BackendError{code, std::move(message), std::move(details)}};
    }
};

struct BackendStatus {
    bool session_active{false};
    std::string channel_state{"CLOSED"};
    std::uint64_t session_id{0};
    std::uint64_t last_event_sequence{0};
    std::size_t queued_events{0};
    std::uint64_t dropped_events{0};
    bool state_change_authorized{false};
    bool unsolicited_enabled{false};
    std::uint8_t unsolicited_class_mask{0};
    std::uint64_t unsolicited_last_sequence{0};
    std::size_t queued_unsolicited_events{0};
    std::uint64_t dropped_unsolicited_events{0};
    std::uint64_t unsolicited_fragments{0};
    Json capture{Json{{"capture_id", nullptr},
                      {"state", "IDLE"},
                      {"valid", nullptr},
                      {"current_queue_depth", 0},
                      {"max_queue_depth", 0},
                      {"queue_overflow", 0}}};
};

class IMasterBackend {
public:
    virtual ~IMasterBackend() = default;

    virtual std::string name() const = 0;
    virtual std::string version() const = 0;
    virtual std::vector<std::string> supported_commands() const = 0;
    virtual Json capabilities() const = 0;
    virtual BackendStatus status() const = 0;

    virtual BackendOperationResult connect(const ConnectionConfig& config) = 0;
    virtual BackendOperationResult disconnect() = 0;
    virtual BackendOperationResult integrity_poll(const ReadOptions& options) = 0;
    virtual BackendOperationResult class_poll(const ClassPollConfig& config) = 0;
    virtual BackendOperationResult read(const ReadConfig& config) = 0;
    virtual BackendOperationResult enable_unsolicited(
        const UnsolicitedControlConfig& config) = 0;
    virtual BackendOperationResult disable_unsolicited(
        const UnsolicitedControlConfig& config) = 0;
    virtual BackendOperationResult wait_unsolicited(
        const WaitUnsolicitedConfig& config) = 0;
    virtual BackendOperationResult capture_begin(const CaptureConfig& config) = 0;
    virtual BackendOperationResult capture_progress(
        const CaptureReferenceConfig& config) = 0;
    virtual BackendOperationResult capture_end(
        const CaptureReferenceConfig& config) = 0;
    virtual BackendOperationResult select_and_operate(const CommandConfig& config) = 0;
    virtual BackendOperationResult direct_operate(const CommandConfig& config) = 0;
    virtual BackendOperationResult wait_event(const WaitEventConfig& config) = 0;
    virtual void shutdown() noexcept = 0;
};

}  // namespace dnp3host
