#include "dnp3host/MeasurementCapture.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace dnp3host {
namespace {

std::uint64_t monotonic_ns() noexcept
{
    const auto duration = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count());
}

const char* mode_name(const CaptureMode mode) noexcept
{
    switch (mode) {
    case CaptureMode::StaticSet:
        return "static_set";
    case CaptureMode::EventSequence:
        return "event_sequence";
    case CaptureMode::Observation:
        return "observation";
    }
    return "observation";
}

enum class CaptureState {
    Idle,
    Active,
    Finalizing,
    Finalized,
    TimedOut,
    Aborted,
};

const char* state_name(const CaptureState state) noexcept
{
    switch (state) {
    case CaptureState::Idle:
        return "IDLE";
    case CaptureState::Active:
        return "ACTIVE";
    case CaptureState::Finalizing:
        return "ACTIVE";
    case CaptureState::Finalized:
        return "FINALIZED";
    case CaptureState::TimedOut:
        return "TIMED_OUT";
    case CaptureState::Aborted:
        return "ABORTED";
    }
    return "ABORTED";
}

struct CaptureMeasurement {
    std::string source;
    std::string kind;
    std::uint8_t group{0};
    std::uint8_t variation{0};
    std::optional<std::uint16_t> index;
    Json value;
    std::uint64_t received_monotonic_ns{0};
};

struct RangeState {
    CapturePointRange range;
    std::vector<std::uint64_t> bits;
};

class Sha256Accumulator final {
public:
    Sha256Accumulator()
    {
        require_success(
            BCryptOpenAlgorithmProvider(
                &algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0),
            "BCryptOpenAlgorithmProvider");
        DWORD object_size = 0;
        DWORD copied = 0;
        try {
            require_success(
                BCryptGetProperty(
                    algorithm_,
                    BCRYPT_OBJECT_LENGTH,
                    reinterpret_cast<PUCHAR>(&object_size),
                    sizeof(object_size),
                    &copied,
                    0),
                "BCryptGetProperty(BCRYPT_OBJECT_LENGTH)");
            if (copied != sizeof(object_size) || object_size == 0U) {
                throw std::runtime_error("BCrypt returned an invalid SHA-256 object size");
            }
            object_.resize(object_size);
            require_success(
                BCryptCreateHash(
                    algorithm_,
                    &hash_,
                    object_.data(),
                    static_cast<ULONG>(object_.size()),
                    nullptr,
                    0,
                    0),
                "BCryptCreateHash");
        }
        catch (...) {
            close();
            throw;
        }
    }

    ~Sha256Accumulator()
    {
        close();
    }

    Sha256Accumulator(const Sha256Accumulator&) = delete;
    Sha256Accumulator& operator=(const Sha256Accumulator&) = delete;

    void update(const std::string_view value)
    {
        if (finished_ || hash_ == nullptr
            || value.size() > std::numeric_limits<ULONG>::max()) {
            throw std::runtime_error("SHA-256 accumulator is not writable");
        }
        require_success(
            BCryptHashData(
                hash_,
                reinterpret_cast<PUCHAR>(const_cast<char*>(value.data())),
                static_cast<ULONG>(value.size()),
                0),
            "BCryptHashData");
    }

    std::string finish()
    {
        if (finished_ || hash_ == nullptr) {
            throw std::runtime_error("SHA-256 accumulator was already finalized");
        }
        std::vector<std::uint8_t> digest(32U);
        require_success(
            BCryptFinishHash(
                hash_, digest.data(), static_cast<ULONG>(digest.size()), 0),
            "BCryptFinishHash");
        finished_ = true;
        static constexpr char hexadecimal[] = "0123456789abcdef";
        std::string encoded;
        encoded.reserve(digest.size() * 2U);
        for (const auto byte : digest) {
            encoded.push_back(hexadecimal[(byte >> 4U) & 0x0FU]);
            encoded.push_back(hexadecimal[byte & 0x0FU]);
        }
        return encoded;
    }

private:
    static void require_success(const NTSTATUS status, const char* operation)
    {
        if (!BCRYPT_SUCCESS(status)) {
            throw std::runtime_error(
                std::string{operation} + " failed with NTSTATUS "
                + std::to_string(static_cast<long>(status)));
        }
    }

    void close() noexcept
    {
        if (hash_ != nullptr) {
            BCryptDestroyHash(hash_);
            hash_ = nullptr;
        }
        if (algorithm_ != nullptr) {
            BCryptCloseAlgorithmProvider(algorithm_, 0);
            algorithm_ = nullptr;
        }
    }

    BCRYPT_ALG_HANDLE algorithm_{nullptr};
    BCRYPT_HASH_HANDLE hash_{nullptr};
    std::vector<std::uint8_t> object_;
    bool finished_{false};
};

std::string lowercase_ascii(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](const char character) {
        return character >= 'A' && character <= 'F'
            ? static_cast<char>(character - 'A' + 'a')
            : character;
    });
    return value;
}

Json event_manifest_json(const CaptureEventManifest& manifest)
{
    return Json{
        {"generator", manifest.generator},
        {"generator_version", manifest.generator_version},
        {"scenario_id", manifest.scenario_id},
        {"seed", manifest.seed},
        {"start_sequence", manifest.start_sequence},
        {"end_sequence", manifest.end_sequence},
        {"event_total", manifest.event_total},
        {"sha256", manifest.sha256},
        {"match_rule", manifest.match_rule}};
}

}  // namespace

struct MeasurementCapture::Impl final {
    Impl()
    {
        // Every member (including queue/state declared after worker) must be
        // initialized before run() can observe this object on another thread.
        worker = std::thread([this] { run(); });
    }

    ~Impl()
    {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            accepting = false;
            if (state == CaptureState::Active || state == CaptureState::Finalizing) {
                add_invalid_reason_locked("CAPTURE_HOST_SHUTDOWN");
                state = CaptureState::Aborted;
                ended_monotonic_ns = monotonic_ns();
                queue.clear();
            }
            condition.notify_all();
        }
        if (worker.joinable()) {
            worker.join();
        }
    }

    BackendOperationResult begin(
        const std::uint64_t session, const CaptureConfig& requested)
    {
        std::lock_guard<std::mutex> lock(mutex);
        expire_locked();
        if (state == CaptureState::Active || state == CaptureState::Finalizing
            || !queue.empty() || processing) {
            return BackendOperationResult::failure(
                ErrorCode::AlreadyExecuting,
                "a measurement capture is already active or draining",
                Json{{"current", snapshot_locked()}});
        }

        try {
            reset_locked();
            config = requested;
            session_id = session;
            capture_id = "cap-" + std::to_string(session) + "-"
                + std::to_string(++next_capture_sequence);
            started_monotonic_ns = monotonic_ns();
            deadline_monotonic_ns = started_monotonic_ns
                + static_cast<std::uint64_t>(config.duration_limit_ms) * 1000000U;
            deadline = std::chrono::steady_clock::now()
                + std::chrono::milliseconds{config.duration_limit_ms};
            for (const auto& requested_range : config.point_ranges) {
                const auto count = static_cast<std::size_t>(requested_range.stop)
                    - requested_range.start + 1U;
                RangeState range;
                range.range = requested_range;
                range.bits.resize((count + 63U) / 64U, 0U);
                ranges.push_back(std::move(range));
                expected_total += count;
            }
            if (config.mode == CaptureMode::EventSequence) {
                sequence_digest = std::make_unique<Sha256Accumulator>();
            }
            state = CaptureState::Active;
            accepting = true;
            condition.notify_all();
            return BackendOperationResult::success(snapshot_locked());
        }
        catch (const std::exception& error) {
            reset_locked();
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "failed to allocate bounded measurement capture storage",
                Json{{"reason", "capture_allocation"},
                     {"backend_message", error.what()}});
        }
        catch (...) {
            reset_locked();
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "failed to allocate bounded measurement capture storage",
                Json{{"reason", "capture_allocation"}});
        }
    }

    BackendOperationResult progress(const CaptureReferenceConfig& reference)
    {
        std::lock_guard<std::mutex> lock(mutex);
        expire_locked();
        if (const auto error = validate_reference_locked(reference.capture_id)) {
            return *error;
        }
        return result_locked();
    }

    BackendOperationResult end(const CaptureReferenceConfig& reference)
    {
        std::unique_lock<std::mutex> lock(mutex);
        expire_locked();
        if (const auto error = validate_reference_locked(reference.capture_id)) {
            return *error;
        }
        if (state == CaptureState::Active) {
            state = CaptureState::Finalizing;
            accepting = false;
            condition.notify_all();
        }

        const auto drained = condition.wait_for(
            lock,
            std::chrono::milliseconds{reference.drain_timeout_ms},
            [this] { return queue.empty() && !processing; });
        if (!drained) {
            discarded_on_abort += queue.size();
            queue.clear();
            accepting = false;
            state = CaptureState::Aborted;
            ended_monotonic_ns = monotonic_ns();
            add_invalid_reason_locked("CAPTURE_DRAIN_TIMEOUT");
            condition.notify_all();
        }
        else if (state == CaptureState::Finalizing) {
            finalize_event_sequence_locked();
            finalize_static_set_locked();
            state = CaptureState::Finalized;
            ended_monotonic_ns = monotonic_ns();
        }
        else if ((state == CaptureState::TimedOut || state == CaptureState::Aborted)
                 && ended_monotonic_ns == 0) {
            ended_monotonic_ns = monotonic_ns();
        }
        if (state == CaptureState::TimedOut || state == CaptureState::Aborted) {
            finalize_event_sequence_locked();
        }
        return result_locked();
    }

    Json abort(const std::string& reason) noexcept
    {
        try {
            std::lock_guard<std::mutex> lock(mutex);
            if (state == CaptureState::Idle) {
                return snapshot_locked();
            }
            if (state == CaptureState::Active || state == CaptureState::Finalizing
                || state == CaptureState::TimedOut) {
                accepting = false;
                discarded_on_abort += queue.size();
                queue.clear();
                state = CaptureState::Aborted;
                ended_monotonic_ns = monotonic_ns();
                add_invalid_reason_locked(reason);
                finalize_event_sequence_locked();
                condition.notify_all();
            }
            return snapshot_locked();
        }
        catch (...) {
            return Json{{"state", "ABORTED"},
                        {"valid", false},
                        {"invalid_reasons", Json::array({"CAPTURE_ABORT_FAILURE"})}};
        }
    }

    Json status() const
    {
        std::lock_guard<std::mutex> lock(mutex);
        const_cast<Impl*>(this)->expire_locked();
        return snapshot_locked();
    }

    void record_fragment(
        const std::string& source, const std::uint64_t received_at) noexcept
    {
        try {
            std::lock_guard<std::mutex> lock(mutex);
            expire_locked();
            if (!accepting || !source_allowed_locked(source)) {
                return;
            }
            if (!first_fragment_monotonic_ns) {
                first_fragment_monotonic_ns = received_at;
            }
            increment_locked(fragments_total);
        }
        catch (...) {
        }
    }

    void record_object(
        std::string source,
        std::string kind,
        const std::uint8_t group,
        const std::uint8_t variation,
        const std::optional<std::uint16_t> index,
        const Json& value,
        const std::uint64_t received_at) noexcept
    {
        try {
            std::lock_guard<std::mutex> lock(mutex);
            expire_locked();
            if (!accepting || !source_allowed_locked(source)) {
                return;
            }
            increment_locked(offered_total);
            if (queue.size() >= config.queue_capacity) {
                increment_locked(queue_overflow);
                add_invalid_reason_locked("CAPTURE_QUEUE_OVERFLOW");
                return;
            }
            queue.push_back(CaptureMeasurement{
                std::move(source),
                std::move(kind),
                group,
                variation,
                index,
                value,
                received_at});
            max_queue_depth = std::max(max_queue_depth, queue.size());
            condition.notify_all();
        }
        catch (...) {
            try {
                std::lock_guard<std::mutex> lock(mutex);
                increment_locked(queue_overflow);
                add_invalid_reason_locked("CAPTURE_ENQUEUE_FAILURE");
            }
            catch (...) {
            }
        }
    }

private:
    void run() noexcept
    {
        std::unique_lock<std::mutex> lock(mutex);
        while (!stopping) {
            expire_locked();
            if (queue.empty()) {
                if (state == CaptureState::Active) {
                    condition.wait_until(lock, deadline, [this] {
                        return stopping || !queue.empty()
                            || state != CaptureState::Active;
                    });
                }
                else {
                    condition.wait(lock, [this] {
                        return stopping || !queue.empty()
                            || state == CaptureState::Active;
                    });
                }
                continue;
            }
            auto measurement = std::move(queue.front());
            queue.pop_front();
            processing = true;
            try {
                process_locked(measurement);
            }
            catch (...) {
                add_invalid_reason_locked("CAPTURE_PROCESSING_FAILURE");
                increment_locked(queue_overflow);
            }
            processing = false;
            condition.notify_all();
        }
    }

    void process_locked(const CaptureMeasurement& measurement)
    {
        increment_locked(received_total);
        if (!first_object_monotonic_ns) {
            first_object_monotonic_ns = measurement.received_monotonic_ns;
        }
        last_object_monotonic_ns = measurement.received_monotonic_ns;
        increment_dimension_locked(by_kind, measurement.kind);
        increment_dimension_locked(
            by_group_variation,
            std::to_string(measurement.group) + ":"
                + std::to_string(measurement.variation));

        if (config.mode == CaptureMode::EventSequence) {
            if (!sequence_digest) {
                throw std::runtime_error("event sequence digest is unavailable");
            }
            const auto canonical = Json::array(
                {measurement.kind,
                 measurement.index ? Json(*measurement.index) : Json(nullptr),
                 measurement.value})
                                       .dump()
                + "\n";
            sequence_digest->update(canonical);
            return;
        }
        if (config.mode != CaptureMode::StaticSet) {
            return;
        }
        if (!measurement.index) {
            record_unmatched_locked(measurement);
            return;
        }
        for (auto& range : ranges) {
            if (range.range.kind != measurement.kind
                || *measurement.index < range.range.start
                || *measurement.index > range.range.stop) {
                continue;
            }
            const auto offset = static_cast<std::size_t>(*measurement.index)
                - range.range.start;
            const auto word = offset / 64U;
            const auto mask = std::uint64_t{1} << (offset % 64U);
            if ((range.bits[word] & mask) != 0U) {
                increment_locked(duplicates);
            }
            else {
                range.bits[word] |= mask;
                increment_locked(received_unique);
            }
            return;
        }
        record_unmatched_locked(measurement);
    }

    void record_unmatched_locked(const CaptureMeasurement& measurement)
    {
        increment_locked(unmatched_total);
        if (mismatch_sample.size() >= config.mismatch_sample_limit) {
            return;
        }
        mismatch_sample.push_back(Json{
            {"reason", "UNEXPECTED_POINT"},
            {"source", measurement.source},
            {"kind", measurement.kind},
            {"group", measurement.group},
            {"variation", measurement.variation},
            {"index", measurement.index ? Json(*measurement.index) : Json(nullptr)},
            {"received_monotonic_ns", measurement.received_monotonic_ns}});
    }

    void increment_dimension_locked(
        std::map<std::string, std::uint64_t>& dimension, const std::string& key)
    {
        auto iterator = dimension.find(key);
        if (iterator == dimension.end()) {
            if (dimension.size() >= 256U) {
                add_invalid_reason_locked("CAPTURE_DIMENSION_LIMIT");
                return;
            }
            iterator = dimension.emplace(key, 0U).first;
        }
        increment_locked(iterator->second);
    }

    void increment_locked(std::uint64_t& value)
    {
        if (value == std::numeric_limits<std::uint64_t>::max()) {
            add_invalid_reason_locked("CAPTURE_COUNTER_OVERFLOW");
            return;
        }
        ++value;
    }

    void add_invalid_reason_locked(const std::string& reason)
    {
        if (std::find(invalid_reasons.begin(), invalid_reasons.end(), reason)
            == invalid_reasons.end()) {
            invalid_reasons.push_back(reason);
        }
    }

    void finalize_event_sequence_locked()
    {
        if (config.mode != CaptureMode::EventSequence
            || received_sequence_sha256.has_value()) {
            return;
        }
        try {
            if (!sequence_digest || !config.event_manifest) {
                throw std::runtime_error("event sequence truth is unavailable");
            }
            received_sequence_sha256 = sequence_digest->finish();
            sequence_digest.reset();
            sequence_match = received_total == config.event_manifest->event_total
                && *received_sequence_sha256
                    == lowercase_ascii(config.event_manifest->sha256);
            if (!*sequence_match) {
                add_invalid_reason_locked("EVENT_SEQUENCE_MISMATCH");
                if (mismatch_sample.size() < config.mismatch_sample_limit) {
                    mismatch_sample.push_back(Json{
                        {"reason", "EVENT_SEQUENCE_MISMATCH"},
                        {"expected_total", config.event_manifest->event_total},
                        {"received_total", received_total},
                        {"expected_sha256",
                         lowercase_ascii(config.event_manifest->sha256)},
                        {"received_sha256", *received_sequence_sha256}});
                }
            }
        }
        catch (...) {
            sequence_digest.reset();
            sequence_match = false;
            add_invalid_reason_locked("EVENT_SEQUENCE_DIGEST_FAILURE");
        }
    }

    void finalize_static_set_locked()
    {
        if (config.mode != CaptureMode::StaticSet) {
            return;
        }
        const auto missing = expected_total >= received_unique
            ? expected_total - received_unique
            : 0U;
        if (missing != 0U || duplicates != 0U || unmatched_total != 0U) {
            add_invalid_reason_locked("STATIC_SET_MISMATCH");
        }
    }

    bool source_allowed_locked(const std::string& source) const
    {
        return std::find(config.sources.begin(), config.sources.end(), source)
            != config.sources.end();
    }

    void expire_locked()
    {
        if (state == CaptureState::Active
            && std::chrono::steady_clock::now() >= deadline) {
            accepting = false;
            state = CaptureState::TimedOut;
            ended_monotonic_ns = deadline_monotonic_ns;
            add_invalid_reason_locked("CAPTURE_DEADLINE_EXCEEDED");
            condition.notify_all();
        }
    }

    std::optional<BackendOperationResult> validate_reference_locked(
        const std::string& requested_id) const
    {
        if (state != CaptureState::Idle && requested_id == capture_id) {
            return std::nullopt;
        }
        return BackendOperationResult::failure(
            ErrorCode::InvalidState,
            "capture_id does not identify the current capture",
            Json{{"requested_capture_id", requested_id},
                 {"current", snapshot_locked()}});
    }

    BackendOperationResult result_locked() const
    {
        auto result = snapshot_locked();
        if (queue_overflow > 0U) {
            return BackendOperationResult::failure(
                ErrorCode::QueueOverflow,
                "measurement capture queue overflowed",
                Json{{"capture_id", capture_id},
                     {"queue_overflow", queue_overflow},
                     {"operation_result", std::move(result)}});
        }
        return BackendOperationResult::success(std::move(result));
    }

    Json snapshot_locked() const
    {
        if (state == CaptureState::Idle) {
            return Json{{"capture_id", nullptr},
                        {"state", "IDLE"},
                        {"valid", nullptr},
                        {"current_queue_depth", 0},
                        {"max_queue_depth", 0},
                        {"queue_overflow", 0}};
        }

        const auto now = monotonic_ns();
        const auto effective_end = ended_monotonic_ns == 0 ? now : ended_monotonic_ns;
        const auto duration_ns = effective_end >= started_monotonic_ns
            ? effective_end - started_monotonic_ns
            : 0U;
        const auto duration_ms = static_cast<double>(duration_ns) / 1000000.0;
        const auto throughput = duration_ns == 0
            ? 0.0
            : static_cast<double>(received_total) * 1000000000.0
                / static_cast<double>(duration_ns);
        const auto terminal = state == CaptureState::Finalized
            || state == CaptureState::TimedOut || state == CaptureState::Aborted;
        const auto valid = state == CaptureState::Finalized && invalid_reasons.empty();

        Json kinds = Json::object();
        for (const auto& entry : by_kind) {
            kinds[entry.first] = entry.second;
        }
        Json group_variations = Json::object();
        for (const auto& entry : by_group_variation) {
            group_variations[entry.first] = entry.second;
        }
        Json sources = Json::array();
        for (const auto& source : config.sources) {
            sources.push_back(source);
        }
        Json reasons = Json::array();
        for (const auto& reason : invalid_reasons) {
            reasons.push_back(reason);
        }

        Json result{
            {"capture_id", capture_id},
            {"session_id", session_id},
            {"state", state_name(state)},
            {"valid", terminal ? Json(valid) : Json(nullptr)},
            {"mode", mode_name(config.mode)},
            {"sources", std::move(sources)},
            {"expected_total",
             config.mode == CaptureMode::StaticSet ? Json(expected_total)
                                                   : config.mode == CaptureMode::EventSequence
                     && config.event_manifest
                 ? Json(config.event_manifest->event_total)
                 : Json(nullptr)},
            {"offered_total", offered_total},
            {"received_total", received_total},
            {"received_unique",
             config.mode == CaptureMode::StaticSet ? Json(received_unique) : Json(nullptr)},
            {"duplicates",
             config.mode == CaptureMode::StaticSet ? Json(duplicates) : Json(nullptr)},
            {"missing",
             config.mode == CaptureMode::StaticSet
                 ? Json(expected_total >= received_unique ? expected_total - received_unique : 0U)
                 : Json(nullptr)},
            {"unmatched_total", unmatched_total},
            {"fragments_total", fragments_total},
            {"duration_ms", duration_ms},
            {"throughput_per_sec", throughput},
            {"current_queue_depth", queue.size()},
            {"max_queue_depth", max_queue_depth},
            {"queue_capacity", config.queue_capacity},
            {"queue_overflow", queue_overflow},
            {"discarded_on_abort", discarded_on_abort},
            {"invalid_reasons", std::move(reasons)},
            {"completeness_scope",
             config.mode == CaptureMode::StaticSet ? "NATIVE_STATIC_SET"
                 : config.mode == CaptureMode::EventSequence
                     && sequence_match == std::optional<bool>{true}
                 ? "EXTERNAL_EVENT_MANIFEST_MATCHED"
                 : config.mode == CaptureMode::EventSequence
                 ? "EXTERNAL_EVENT_MANIFEST_REQUIRED"
                 : "OBSERVATION_ONLY"},
            {"unknown_reason",
             config.mode == CaptureMode::StaticSet
                 ? Json(nullptr)
                 : config.mode == CaptureMode::EventSequence
                     && sequence_match == std::optional<bool>{true}
                 ? Json(nullptr)
                 : config.mode == CaptureMode::EventSequence
                 ? Json(
                     received_sequence_sha256
                         ? "EVENT_SEQUENCE_MISMATCH_REQUIRES_MANIFEST_DIFF"
                         : "EVENT_SEQUENCE_NOT_FINALIZED")
                 : Json("UNKNOWN_WITHOUT_GROUND_TRUTH_MATCH")},
            {"received_sequence_sha256",
             received_sequence_sha256 ? Json(*received_sequence_sha256)
                                      : Json(nullptr)},
            {"sequence_match",
             sequence_match ? Json(*sequence_match) : Json(nullptr)},
            {"canonical_record_format",
             config.mode == CaptureMode::EventSequence
                 ? Json("compact-json-array-[kind,index,value]-plus-LF")
                 : Json(nullptr)},
            {"mismatch_sample", mismatch_sample},
            {"mismatch_sample_limit", config.mismatch_sample_limit},
            {"by_kind", std::move(kinds)},
            {"by_group_variation", std::move(group_variations)},
            {"timings",
             Json{{"started_monotonic_ns", started_monotonic_ns},
                  {"deadline_monotonic_ns", deadline_monotonic_ns},
                  {"first_fragment_monotonic_ns",
                   first_fragment_monotonic_ns ? Json(*first_fragment_monotonic_ns)
                                               : Json(nullptr)},
                  {"first_object_monotonic_ns",
                   first_object_monotonic_ns ? Json(*first_object_monotonic_ns)
                                             : Json(nullptr)},
                  {"last_object_monotonic_ns",
                   last_object_monotonic_ns ? Json(*last_object_monotonic_ns)
                                            : Json(nullptr)},
                  {"ended_monotonic_ns",
                   ended_monotonic_ns == 0 ? Json(nullptr) : Json(ended_monotonic_ns)}}}};
        if (config.event_manifest) {
            result["event_manifest"] = event_manifest_json(*config.event_manifest);
        }
        else {
            result["event_manifest"] = nullptr;
        }
        return result;
    }

    void reset_locked()
    {
        state = CaptureState::Idle;
        accepting = false;
        config = CaptureConfig{};
        capture_id.clear();
        session_id = 0;
        queue.clear();
        ranges.clear();
        expected_total = 0;
        offered_total = 0;
        received_total = 0;
        received_unique = 0;
        duplicates = 0;
        unmatched_total = 0;
        fragments_total = 0;
        queue_overflow = 0;
        discarded_on_abort = 0;
        max_queue_depth = 0;
        processing = false;
        mismatch_sample = Json::array();
        by_kind.clear();
        by_group_variation.clear();
        invalid_reasons.clear();
        started_monotonic_ns = 0;
        deadline_monotonic_ns = 0;
        ended_monotonic_ns = 0;
        first_fragment_monotonic_ns.reset();
        first_object_monotonic_ns.reset();
        last_object_monotonic_ns.reset();
        sequence_digest.reset();
        received_sequence_sha256.reset();
        sequence_match.reset();
    }

    mutable std::mutex mutex;
    std::condition_variable condition;
    std::thread worker;
    bool stopping{false};
    bool accepting{false};
    bool processing{false};
    CaptureState state{CaptureState::Idle};
    CaptureConfig config;
    std::string capture_id;
    std::uint64_t session_id{0};
    std::uint64_t next_capture_sequence{0};
    std::chrono::steady_clock::time_point deadline{};
    std::deque<CaptureMeasurement> queue;
    std::vector<RangeState> ranges;
    std::uint64_t expected_total{0};
    std::uint64_t offered_total{0};
    std::uint64_t received_total{0};
    std::uint64_t received_unique{0};
    std::uint64_t duplicates{0};
    std::uint64_t unmatched_total{0};
    std::uint64_t fragments_total{0};
    std::uint64_t queue_overflow{0};
    std::uint64_t discarded_on_abort{0};
    std::size_t max_queue_depth{0};
    Json mismatch_sample{Json::array()};
    std::map<std::string, std::uint64_t> by_kind;
    std::map<std::string, std::uint64_t> by_group_variation;
    std::vector<std::string> invalid_reasons;
    std::uint64_t started_monotonic_ns{0};
    std::uint64_t deadline_monotonic_ns{0};
    std::uint64_t ended_monotonic_ns{0};
    std::optional<std::uint64_t> first_fragment_monotonic_ns;
    std::optional<std::uint64_t> first_object_monotonic_ns;
    std::optional<std::uint64_t> last_object_monotonic_ns;
    std::unique_ptr<Sha256Accumulator> sequence_digest;
    std::optional<std::string> received_sequence_sha256;
    std::optional<bool> sequence_match;
};

MeasurementCapture::MeasurementCapture()
    : impl_(std::make_unique<Impl>())
{
}

MeasurementCapture::~MeasurementCapture() = default;

BackendOperationResult MeasurementCapture::begin(
    const std::uint64_t session_id, const CaptureConfig& config)
{
    return impl_->begin(session_id, config);
}

BackendOperationResult MeasurementCapture::progress(
    const CaptureReferenceConfig& config)
{
    return impl_->progress(config);
}

BackendOperationResult MeasurementCapture::end(const CaptureReferenceConfig& config)
{
    return impl_->end(config);
}

Json MeasurementCapture::abort(const std::string& reason) noexcept
{
    return impl_->abort(reason);
}

Json MeasurementCapture::status() const
{
    return impl_->status();
}

void MeasurementCapture::record_fragment(
    const std::string& source, const std::uint64_t received_monotonic_ns) noexcept
{
    impl_->record_fragment(source, received_monotonic_ns);
}

void MeasurementCapture::record_object(
    const std::string& source,
    const std::string& kind,
    const std::uint8_t group,
    const std::uint8_t variation,
    const std::optional<std::uint16_t> index,
    const Json& value,
    const std::uint64_t received_monotonic_ns) noexcept
{
    impl_->record_object(
        source, kind, group, variation, index, value, received_monotonic_ns);
}

}  // namespace dnp3host
