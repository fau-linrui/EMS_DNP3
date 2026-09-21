#include "dnp3host/ProtocolTrace.h"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <limits>

namespace dnp3host {
namespace {

// Preserve valid UTF-8, including boundaries, without unbounded strlen or any
// allocation. Invalid input is replaced by '?' and explicitly marks lost fidelity.
bool copy_utf8(const char* source, char* target, const std::size_t maximum) noexcept
{
    if (!source) {
        target[0] = '\0';
        return true;
    }
    std::size_t read = 0;
    std::size_t written = 0;
    bool changed = false;
    while (source[read] != '\0') {
        const auto first = static_cast<unsigned char>(source[read]);
        std::size_t length = 1;
        if (first >= 0xC2U && first <= 0xDFU) {
            length = 2;
        }
        else if (first >= 0xE0U && first <= 0xEFU) {
            length = 3;
        }
        else if (first >= 0xF0U && first <= 0xF4U) {
            length = 4;
        }
        bool valid = first < 0x80U || length > 1;
        for (std::size_t index = 1; valid && index < length; ++index) {
            const auto next = static_cast<unsigned char>(source[read + index]);
            valid = next >= 0x80U && next <= 0xBFU;
            if (index == 1) {
                valid = valid && !(first == 0xE0U && next < 0xA0U)
                    && !(first == 0xEDU && next >= 0xA0U)
                    && !(first == 0xF0U && next < 0x90U)
                    && !(first == 0xF4U && next >= 0x90U);
            }
        }
        if (!valid) {
            if (written == maximum) {
                changed = true;
                break;
            }
            target[written++] = '?';
            ++read;
            changed = true;
        }
        else {
            if (length > maximum - written) {
                changed = true;
                break;
            }
            std::memcpy(target + written, source + read, length);
            written += length;
            read += length;
        }
    }
    target[written] = '\0';
    return changed;
}

const char* level_name(const std::int32_t level) noexcept
{
    static constexpr std::array<const char*, 17> names{
        "EVENT", "ERR", "WARN", "INFO", "DBG", "LINK_RX", "LINK_RX_HEX",
        "LINK_TX", "LINK_TX_HEX", "TRANSPORT_RX", "TRANSPORT_TX", "APP_HEADER_RX",
        "APP_HEADER_TX", "APP_OBJECT_RX", "APP_OBJECT_TX", "APP_HEX_RX", "APP_HEX_TX"};
    for (std::size_t index = 0; index < names.size(); ++index) {
        if (level == static_cast<std::int32_t>(1U << index)) {
            return names[index];
        }
    }
    return "OTHER";
}

BackendOperationResult invalid_state(const char* reason)
{
    return BackendOperationResult::failure(
        ErrorCode::InvalidState, "protocol trace operation is not allowed",
        Json{{"reason", reason}});
}

}  // namespace

ProtocolTrace::Snapshot ProtocolTrace::snapshot_locked() const
{
    return Snapshot{trace_id_, trace_id_.empty() ? "IDLE" : (active_ ? "ACTIVE" : "STOPPED"),
                    capacity_, queued_, dropped_, truncated_, sequence_};
}

Json ProtocolTrace::serialize(const Snapshot& snapshot)
{
    return Json{{"trace_id", snapshot.trace_id.empty() ? Json(nullptr) : Json(snapshot.trace_id)},
                {"state", snapshot.state}, {"scope", "opendnp3_stack"},
                {"queue_capacity", snapshot.capacity}, {"queued_records", snapshot.queued},
                {"dropped_records", snapshot.dropped}, {"truncated_records", snapshot.truncated},
                {"last_sequence", snapshot.sequence},
                {"complete", snapshot.dropped == 0 && snapshot.truncated == 0}};
}

BackendOperationResult ProtocolTrace::start(const TraceStartConfig& config)
{
    if (config.queue_capacity == 0 || config.queue_capacity > 65536) {
        return BackendOperationResult::failure(
            ErrorCode::InvalidRequest, "trace queue capacity is out of range");
    }
    Snapshot snapshot;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (active_) {
            return invalid_state("trace_already_active");
        }
        if (queued_ != 0) {
            return invalid_state("unread_trace_records");
        }
        if (next_trace_ == std::numeric_limits<std::uint64_t>::max()) {
            return invalid_state("trace_id_exhausted");
        }
        // Allocate before mutating the prior state. No allocations on log callbacks.
        std::vector<Record> prepared(config.queue_capacity);
        auto id = "trace-" + std::to_string(next_trace_ + 1U);
        records_.swap(prepared);
        trace_id_.swap(id);
        ++next_trace_;
        capacity_ = config.queue_capacity;
        head_ = queued_ = 0;
        dropped_ = truncated_ = sequence_ = 0;
        active_ = true;
        snapshot = snapshot_locked();
    }
    return BackendOperationResult::success(serialize(snapshot));
}

BackendOperationResult ProtocolTrace::read(const TraceReferenceConfig& config)
{
    if (config.max_records == 0 || config.max_records > 1024 || config.timeout_ms > 60000) {
        return BackendOperationResult::failure(
            ErrorCode::InvalidRequest, "trace read bounds are invalid");
    }
    std::vector<Record> selected;
    selected.reserve(config.max_records);
    Snapshot snapshot;
    {
        std::unique_lock<std::mutex> lock(mutex_);
        if (trace_id_.empty() || config.trace_id != trace_id_) {
            return invalid_state("unknown_trace_id");
        }
        if (queued_ == 0 && active_ && config.timeout_ms > 0) {
            condition_.wait_for(lock, std::chrono::milliseconds{config.timeout_ms},
                                [this] { return queued_ != 0 || !active_; });
        }
        const auto count = std::min(queued_, config.max_records);
        for (std::size_t index = 0; index < count; ++index) {
            selected.push_back(records_[head_]);
            head_ = (head_ + 1U) % capacity_;
        }
        queued_ -= count;
        snapshot = snapshot_locked();
    }
    auto result = serialize(snapshot);
    auto records = Json::array();
    for (const auto& record : selected) {
        records.push_back(Json{
            {"sequence", record.sequence}, {"session_id", record.session_id},
            {"monotonic_ns", record.monotonic_ns}, {"logger", record.logger.data()},
            {"level", record.level}, {"message", record.message.data()},
            {"message_truncated", record.message_truncated}});
    }
    result["records"] = std::move(records);
    result["timed_out"] = selected.empty();
    return BackendOperationResult::success(std::move(result));
}

BackendOperationResult ProtocolTrace::stop(const TraceReferenceConfig& config)
{
    Snapshot snapshot;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (trace_id_.empty() || config.trace_id != trace_id_) {
            return invalid_state("unknown_trace_id");
        }
        active_ = false;
        snapshot = snapshot_locked();
        condition_.notify_all();
    }
    return BackendOperationResult::success(serialize(snapshot));
}

Json ProtocolTrace::status() const
{
    Snapshot snapshot;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot = snapshot_locked();
    }
    return serialize(snapshot);
}

bool ProtocolTrace::active() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return active_;
}

void ProtocolTrace::shutdown() noexcept
{
    std::lock_guard<std::mutex> lock(mutex_);
    active_ = false;
    condition_.notify_all();
}

void ProtocolTrace::record(const std::uint64_t session_id, const char* logger,
                           const std::int32_t level, const char* message) noexcept
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!active_ || session_id == 0) {
        return;
    }
    // Saturation is practically unreachable, but must never wrap a sequence and
    // silently describe a broken observation stream as complete.
    if (sequence_ == std::numeric_limits<std::uint64_t>::max()) {
        dropped_ = std::numeric_limits<std::uint64_t>::max();
        return;
    }
    if (queued_ == capacity_) {
        head_ = (head_ + 1U) % capacity_;
        --queued_;
        if (dropped_ != std::numeric_limits<std::uint64_t>::max()) {
            ++dropped_;
        }
    }
    auto& record = records_[(head_ + queued_) % capacity_];
    record.sequence = ++sequence_;
    record.session_id = session_id;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    record.monotonic_ns = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
    record.level = level_name(level);
    const auto logger_changed = copy_utf8(logger, record.logger.data(), 128);
    const auto message_changed = copy_utf8(message, record.message.data(), 1024);
    record.message_truncated = logger_changed || message_changed;
    if (record.message_truncated && truncated_ != std::numeric_limits<std::uint64_t>::max()) {
        ++truncated_;
    }
    ++queued_;
    condition_.notify_all();
}

}  // namespace dnp3host
