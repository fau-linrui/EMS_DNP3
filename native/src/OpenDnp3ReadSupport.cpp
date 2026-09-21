#include "dnp3host/OpenDnp3ReadSupport.h"
#include "dnp3host/Ieee1815_2012.h"
#include "dnp3host/MeasurementCapture.h"

#include <opendnp3/app/IINField.h>
#include <opendnp3/app/MeasurementTypes.h>
#include <opendnp3/app/OctetString.h>
#include <opendnp3/gen/CommandStatus.h>
#include <opendnp3/gen/DoubleBit.h>
#include <opendnp3/gen/GroupVariation.h>
#include <opendnp3/gen/IntervalUnits.h>
#include <opendnp3/gen/QualifierCode.h>
#include <opendnp3/gen/TaskCompletion.h>
#include <opendnp3/gen/TimestampQuality.h>
#include <opendnp3/master/HeaderInfo.h>
#include <opendnp3/master/HeaderTypes.h>
#include <opendnp3/master/IMaster.h>
#include <opendnp3/master/IMasterApplication.h>
#include <opendnp3/master/ISOEHandler.h>
#include <opendnp3/master/ITaskCallback.h>
#include <opendnp3/master/ResponseInfo.h>
#include <opendnp3/master/TaskConfig.h>
#include <opendnp3/util/UTCTimestamp.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace dnp3host {
namespace {

constexpr std::size_t kIinObservationCapacity = 1024;
constexpr std::size_t kFragmentRecordCapacity = 4096;

std::uint64_t monotonic_ns() noexcept
{
    const auto duration = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count());
}

std::uint64_t utc_ms() noexcept
{
    const auto duration = std::chrono::system_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(duration).count());
}

const char* return_mode_name(const ReturnMode mode) noexcept
{
    return mode == ReturnMode::Detail ? "detail" : "summary";
}

std::string byte_hex(const std::uint8_t value)
{
    static constexpr char digits[] = "0123456789ABCDEF";
    std::string result(2, '0');
    result[0] = digits[(value >> 4U) & 0x0FU];
    result[1] = digits[value & 0x0FU];
    return result;
}

std::string bytes_hex(const opendnp3::Buffer& buffer)
{
    static constexpr char digits[] = "0123456789ABCDEF";
    std::string result(buffer.length * 2U, '0');
    for (std::size_t index = 0; index < buffer.length; ++index) {
        const auto value = buffer.data[index];
        result[index * 2U] = digits[(value >> 4U) & 0x0FU];
        result[index * 2U + 1U] = digits[value & 0x0FU];
    }
    return result;
}

struct IinObservation {
    std::uint64_t sequence{0};
    std::uint64_t observed_monotonic_ns{0};
    std::uint8_t lsb{0};
    std::uint8_t msb{0};
};

class IinStore final {
public:
    void record(const opendnp3::IINField& value)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (observations_.size() == kIinObservationCapacity) {
            observations_.pop_front();
            ++dropped_;
        }
        observations_.push_back(IinObservation{
            ++last_sequence_, monotonic_ns(), value.LSB, value.MSB});
    }

    std::uint64_t last_sequence() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return last_sequence_;
    }

    std::vector<IinObservation> after(
        const std::uint64_t sequence,
        std::uint64_t& dropped_total,
        std::uint64_t& window_dropped) const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<IinObservation> result;
        for (const auto& observation : observations_) {
            if (observation.sequence > sequence) {
                result.push_back(observation);
            }
        }
        dropped_total = dropped_;
        const auto window_total = last_sequence_ >= sequence
            ? last_sequence_ - sequence
            : 0U;
        window_dropped = window_total > result.size()
            ? window_total - result.size()
            : 0U;
        return result;
    }

private:
    mutable std::mutex mutex_;
    std::deque<IinObservation> observations_;
    std::uint64_t last_sequence_{0};
    std::uint64_t dropped_{0};
};

class ReadOperation;

class CompletionGate final {
public:
    void set_active(const std::shared_ptr<ReadOperation>& operation)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        active_ = operation;
    }

    void clear(const std::shared_ptr<ReadOperation>& operation)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (active_.lock() == operation) {
            active_.reset();
        }
    }

    void on_iin_processed();

private:
    std::mutex mutex_;
    std::weak_ptr<ReadOperation> active_;
};

Json iin_json(
    const std::vector<IinObservation>& observations,
    const std::uint64_t dropped_total,
    const std::uint64_t window_dropped)
{
    std::uint8_t aggregate_lsb = 0;
    std::uint8_t aggregate_msb = 0;
    auto serialized = Json::array();
    for (const auto& observation : observations) {
        aggregate_lsb = static_cast<std::uint8_t>(aggregate_lsb | observation.lsb);
        aggregate_msb = static_cast<std::uint8_t>(aggregate_msb | observation.msb);
        serialized.push_back(Json{
            {"sequence", observation.sequence},
            {"observed_monotonic_ns", observation.observed_monotonic_ns},
            {"lsb", observation.lsb},
            {"msb", observation.msb},
            {"raw_hex", byte_hex(observation.lsb) + byte_hex(observation.msb)}});
    }

    auto bits = Json::array();
    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        if ((aggregate_lsb & static_cast<std::uint8_t>(1U << bit)) != 0U) {
            bits.push_back(std::string{"IIN1."} + std::to_string(bit) + "."
                           + std::string{kIin1Names2012[bit]});
        }
        if ((aggregate_msb & static_cast<std::uint8_t>(1U << bit)) != 0U) {
            bits.push_back(std::string{"IIN2."} + std::to_string(bit) + "."
                           + std::string{kIin2Names2012[bit]});
        }
    }

    return Json{
        {"correlation", "master_callback_task_window"},
        {"lsb", aggregate_lsb},
        {"msb", aggregate_msb},
        {"raw_hex", byte_hex(aggregate_lsb) + byte_hex(aggregate_msb)},
        {"bits", std::move(bits)},
        {"observations", std::move(serialized)},
        {"observation_store_dropped_total", dropped_total},
        {"observation_window_dropped", window_dropped},
        {"observation_store_capacity", kIinObservationCapacity}};
}

class TrackingMasterApplication final : public opendnp3::IMasterApplication {
public:
    TrackingMasterApplication(
        std::shared_ptr<IinStore> iin,
        std::shared_ptr<CompletionGate> completion_gate)
        : iin_(std::move(iin)), completion_gate_(std::move(completion_gate))
    {
    }

    void OnReceiveIIN(const opendnp3::IINField& iin) override
    {
        iin_->record(iin);
        completion_gate_->on_iin_processed();
    }

    opendnp3::UTCTimestamp Now() override
    {
        return opendnp3::UTCTimestamp{utc_ms()};
    }

private:
    std::shared_ptr<IinStore> iin_;
    std::shared_ptr<CompletionGate> completion_gate_;
};

class ReadOperation final {
public:
    ReadOperation(
        const int task_id,
        const ReadOptions options,
        std::shared_ptr<std::atomic<std::uint64_t>> receive_sequence,
        const std::uint64_t iin_start_sequence,
        std::shared_ptr<MeasurementCapture> capture)
        : task_id_(task_id),
          options_(options),
          receive_sequence_(std::move(receive_sequence)),
          iin_start_sequence_(iin_start_sequence),
          submitted_monotonic_ns_(monotonic_ns()),
          capture_(std::move(capture))
    {
    }

    int task_id() const noexcept
    {
        return task_id_;
    }

    std::uint64_t iin_start_sequence() const noexcept
    {
        return iin_start_sequence_;
    }

    bool is_done() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return done_;
    }

    void mark_started()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!started_) {
            started_ = true;
            started_monotonic_ns_ = monotonic_ns();
        }
    }

    void mark_complete(const opendnp3::TaskCompletion completion)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (done_) {
            return;
        }
        pending_completion_ = completion;
        if (completion != opendnp3::TaskCompletion::SUCCESS
            && completion != opendnp3::TaskCompletion::FAILURE_BAD_RESPONSE) {
            finalize_locked(completion);
        }
    }

    void finalize_response_after_iin()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!done_ && pending_completion_) {
            // In the fixed OpenDNP3 3.1.2 MContext::ProcessResponse path,
            // ITaskCallback::OnComplete is invoked before OnReceiveIIN for the
            // same response. Ignore earlier IIN callbacks (for example, an
            // unsolicited response) and release only after a completion is
            // pending, so the operation cannot consume another response's IIN.
            finalize_locked(*pending_completion_);
        }
    }

    void mark_destroyed()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        destroyed_ = true;
        if (!done_ && !pending_completion_) {
            // A response-bearing task can be destroyed immediately after
            // OnComplete, while MContext has not called OnReceiveIIN yet.
            // Keep SUCCESS/FAILURE_BAD_RESPONSE pending so that the response
            // IIN is recorded before the waiting API thread is released.
            finalize_locked(opendnp3::TaskCompletion::FAILURE_NO_COMMS);
        }
        condition_.notify_all();
    }

    void cancel() noexcept
    {
        try {
            std::lock_guard<std::mutex> lock(mutex_);
            destroyed_ = true;
            if (!done_) {
                pending_completion_.reset();
                finalize_locked(opendnp3::TaskCompletion::FAILURE_NO_COMMS);
            }
            condition_.notify_all();
        }
        catch (...) {
        }
    }

    void begin_fragment(const opendnp3::ResponseInfo& info)
    {
        const auto began_at = monotonic_ns();
        std::lock_guard<std::mutex> lock(mutex_);
        if (!first_fragment_monotonic_ns_) {
            first_fragment_monotonic_ns_ = began_at;
        }
        current_fragment_index_ = fragment_count_++;
        current_unsolicited_ = info.unsolicited;
        if (capture_) {
            capture_->record_fragment(
                info.unsolicited ? "unsolicited" : "solicited", began_at);
        }
        current_fragment_recorded_ = fragments_.size() < kFragmentRecordCapacity;
        if (!current_fragment_recorded_) {
            ++fragment_overflow_;
            return;
        }
        fragments_.push_back(Json{
            {"fragment_index", current_fragment_index_},
            {"source", info.unsolicited ? "unsolicited" : "solicited"},
            {"fir", info.fir},
            {"fin", info.fin},
            {"begin_monotonic_ns", began_at},
            {"ended", false}});
    }

    void end_fragment(const opendnp3::ResponseInfo&)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (current_fragment_recorded_ && !fragments_.empty()) {
            fragments_.back()["ended"] = true;
            fragments_.back()["end_monotonic_ns"] = monotonic_ns();
        }
        current_fragment_recorded_ = false;
    }

    void record(
        const opendnp3::HeaderInfo& info,
        const std::optional<std::uint16_t> index,
        const std::string& kind,
        Json value,
        const std::optional<std::uint8_t> flags,
        const opendnp3::DNPTime time,
        Json extra = Json::object())
    {
        const auto receive_sequence =
            receive_sequence_->fetch_add(1, std::memory_order_relaxed) + 1U;
        const auto received_at = monotonic_ns();
        const auto encoded_gv = opendnp3::GroupVariationSpec::to_type(info.gv);
        const auto group = static_cast<std::uint8_t>((encoded_gv >> 8U) & 0xFFU);
        const auto variation = static_cast<std::uint8_t>(encoded_gv & 0xFFU);

        std::lock_guard<std::mutex> lock(mutex_);
        if (!first_object_monotonic_ns_) {
            first_object_monotonic_ns_ = received_at;
        }
        last_object_monotonic_ns_ = received_at;
        if (capture_) {
            capture_->record_object(
                current_unsolicited_ ? "unsolicited" : "solicited",
                kind,
                group,
                variation,
                index,
                value,
                received_at);
        }
        ++received_total_;
        ++by_kind_[kind];
        ++by_group_variation_[
            std::to_string(group) + ":" + std::to_string(variation)];
        if (!first_receive_sequence_) {
            first_receive_sequence_ = receive_sequence;
        }
        last_receive_sequence_ = receive_sequence;

        if (received_total_ > options_.max_measurements) {
            ++overflow_;
            return;
        }
        if (options_.return_mode == ReturnMode::Summary) {
            return;
        }

        Json record{
            {"receive_seq", receive_sequence},
            {"received_monotonic_ns", received_at},
            {"kind", kind},
            {"group", group},
            {"variation", variation},
            {"qualifier", opendnp3::QualifierCodeSpec::to_string(info.qualifier)},
            {"qualifier_raw", opendnp3::QualifierCodeSpec::to_type(info.qualifier)},
            {"index", index ? Json(*index) : Json(nullptr)},
            {"value", std::move(value)},
            {"flags_raw", flags ? Json(*flags) : Json(nullptr)},
            {"flags_valid", info.flagsValid},
            {"dnp3_timestamp_ms",
             info.tsquality == opendnp3::TimestampQuality::INVALID
                 ? Json(nullptr)
                 : Json(time.value)},
            {"timestamp_quality",
             opendnp3::TimestampQualitySpec::to_string(info.tsquality)},
            {"is_event", info.isEventVariation},
            {"header_index", info.headerIndex},
            {"source", current_unsolicited_ ? "unsolicited" : "solicited"},
            {"fragment_index", current_fragment_index_}};
        for (auto iterator = extra.begin(); iterator != extra.end(); ++iterator) {
            record[iterator.key()] = iterator.value();
        }
        measurements_.push_back(std::move(record));
    }

    void wait_for_completion()
    {
        std::unique_lock<std::mutex> lock(mutex_);
        const auto completed = condition_.wait_for(
            lock,
            std::chrono::milliseconds{options_.timeout_ms},
            [this] { return done_; });
        // Freeze this API call's deadline decision while holding the same
        // mutex as completion. A late response may finish the underlying task,
        // but must not upgrade this call to SUCCESS using an earlier IIN
        // snapshot taken between the expired wait and that response's IIN.
        wait_timed_out_ = wait_timed_out_ || !completed;
    }

    BackendOperationResult outcome(
        const std::vector<IinObservation>& iin_observations,
        const std::uint64_t iin_dropped_total,
        const std::uint64_t iin_window_dropped) const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (wait_timed_out_ || !done_ || !completion_) {
            return BackendOperationResult::failure(
                ErrorCode::ResponseTimeout,
                "DNP3 read task did not complete before the configured deadline",
                Json{{"task_id", task_id_},
                     {"timeout_ms", options_.timeout_ms},
                     {"task_started", started_},
                     {"received_total", received_total_}});
        }

        auto by_kind = Json::object();
        for (const auto& entry : by_kind_) {
            by_kind[entry.first] = entry.second;
        }
        auto by_group_variation = Json::object();
        for (const auto& entry : by_group_variation_) {
            by_group_variation[entry.first] = entry.second;
        }

        const auto completed_at = completed_monotonic_ns_.value_or(monotonic_ns());
        const auto duration_ns = completed_at >= submitted_monotonic_ns_
            ? completed_at - submitted_monotonic_ns_
            : 0U;
        Json result{
            {"task_id", task_id_},
            {"task_status", opendnp3::TaskCompletionSpec::to_string(*completion_)},
            {"task_started", started_},
            {"task_destroyed", destroyed_},
            {"return_mode", return_mode_name(options_.return_mode)},
            {"measurements", measurements_},
            {"summary",
             Json{{"received_total", received_total_},
                  {"stored_detail", measurements_.size()},
                  {"overflow", overflow_},
                  {"fragments_received", fragment_count_},
                  {"fragments_stored", fragments_.size()},
                  {"fragment_overflow", fragment_overflow_},
                  {"max_fragments", kFragmentRecordCapacity},
                  {"max_measurements", options_.max_measurements},
                  {"first_receive_seq",
                   first_receive_sequence_ ? Json(*first_receive_sequence_) : Json(nullptr)},
                  {"last_receive_seq",
                   last_receive_sequence_ ? Json(*last_receive_sequence_) : Json(nullptr)},
                  {"by_kind", std::move(by_kind)},
                  {"by_group_variation", std::move(by_group_variation)}}},
            {"fragments", fragments_},
            {"iin",
             iin_json(
                 iin_observations, iin_dropped_total, iin_window_dropped)},
            {"timings",
             Json{{"submitted_monotonic_ns", submitted_monotonic_ns_},
                  {"started_monotonic_ns",
                   started_monotonic_ns_ ? Json(*started_monotonic_ns_) : Json(nullptr)},
                  {"first_fragment_monotonic_ns",
                   first_fragment_monotonic_ns_
                       ? Json(*first_fragment_monotonic_ns_)
                       : Json(nullptr)},
                  {"first_object_monotonic_ns",
                   first_object_monotonic_ns_ ? Json(*first_object_monotonic_ns_)
                                              : Json(nullptr)},
                  {"last_object_monotonic_ns",
                   last_object_monotonic_ns_ ? Json(*last_object_monotonic_ns_)
                                             : Json(nullptr)},
                  {"completed_monotonic_ns", completed_at},
                  {"duration_ms", static_cast<double>(duration_ns) / 1000000.0}}}};

        if (overflow_ > 0 || fragment_overflow_ > 0 || iin_window_dropped > 0) {
            return BackendOperationResult::failure(
                ErrorCode::QueueOverflow,
                "read result metadata exceeded a bounded collection capacity",
                Json{{"task_id", task_id_},
                     {"overflow", overflow_},
                     {"fragment_overflow", fragment_overflow_},
                     {"iin_observation_window_dropped", iin_window_dropped},
                     {"operation_result", std::move(result)}});
        }
        if (*completion_ == opendnp3::TaskCompletion::SUCCESS) {
            return BackendOperationResult::success(std::move(result));
        }

        const auto code = *completion_ == opendnp3::TaskCompletion::FAILURE_RESPONSE_TIMEOUT
            ? ErrorCode::ResponseTimeout
            : ErrorCode::TaskFailed;
        return BackendOperationResult::failure(
            code,
            "DNP3 read task did not complete successfully",
            Json{{"task_id", task_id_},
                 {"task_status", opendnp3::TaskCompletionSpec::to_string(*completion_)},
                 {"operation_result", std::move(result)}});
    }

private:
    void finalize_locked(const opendnp3::TaskCompletion completion)
    {
        completion_ = completion;
        completed_monotonic_ns_ = monotonic_ns();
        done_ = true;
        condition_.notify_all();
    }

    const int task_id_;
    const ReadOptions options_;
    std::shared_ptr<std::atomic<std::uint64_t>> receive_sequence_;
    const std::uint64_t iin_start_sequence_;
    const std::uint64_t submitted_monotonic_ns_;
    std::shared_ptr<MeasurementCapture> capture_;

    mutable std::mutex mutex_;
    std::condition_variable condition_;
    bool started_{false};
    bool done_{false};
    bool wait_timed_out_{false};
    bool destroyed_{false};
    std::optional<opendnp3::TaskCompletion> pending_completion_;
    std::optional<opendnp3::TaskCompletion> completion_;
    std::optional<std::uint64_t> started_monotonic_ns_;
    std::optional<std::uint64_t> completed_monotonic_ns_;
    std::optional<std::uint64_t> first_fragment_monotonic_ns_;
    std::optional<std::uint64_t> first_object_monotonic_ns_;
    std::optional<std::uint64_t> last_object_monotonic_ns_;
    std::uint64_t fragment_count_{0};
    std::uint64_t current_fragment_index_{0};
    bool current_unsolicited_{false};
    bool current_fragment_recorded_{false};
    Json fragments_{Json::array()};
    Json measurements_{Json::array()};
    std::uint64_t received_total_{0};
    std::uint64_t overflow_{0};
    std::uint64_t fragment_overflow_{0};
    std::optional<std::uint64_t> first_receive_sequence_;
    std::optional<std::uint64_t> last_receive_sequence_;
    std::map<std::string, std::uint64_t> by_kind_;
    std::map<std::string, std::uint64_t> by_group_variation_;
};

void CompletionGate::on_iin_processed()
{
    std::shared_ptr<ReadOperation> operation;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        operation = active_.lock();
    }
    if (operation) {
        operation->finalize_response_after_iin();
    }
}

class ReadTaskCallback final : public opendnp3::ITaskCallback {
public:
    explicit ReadTaskCallback(std::shared_ptr<ReadOperation> operation)
        : operation_(std::move(operation))
    {
    }

    void OnStart() override
    {
        operation_->mark_started();
    }

    void OnComplete(const opendnp3::TaskCompletion result) override
    {
        operation_->mark_complete(result);
    }

    void OnDestroyed() override
    {
        operation_->mark_destroyed();
    }

private:
    std::shared_ptr<ReadOperation> operation_;
};

Json finite_number(const double value, Json& extra)
{
    if (std::isfinite(value)) {
        return Json(value);
    }
    extra["value_class"] = std::isnan(value) ? "nan"
        : value > 0.0                           ? "positive_infinity"
                                                : "negative_infinity";
    return Json(nullptr);
}

class CollectingSoeHandler final : public opendnp3::ISOEHandler {
public:
    explicit CollectingSoeHandler(std::shared_ptr<ReadOperation> operation)
        : operation_(std::move(operation))
    {
    }

    void BeginFragment(const opendnp3::ResponseInfo& info) override
    {
        operation_->begin_fragment(info);
    }

    void EndFragment(const opendnp3::ResponseInfo& info) override
    {
        operation_->end_fragment(info);
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Binary>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "binary_input",
                Json(item.value.value),
                item.value.flags.value,
                item.value.time);
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::DoubleBitBinary>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "double_bit_binary_input",
                Json(std::string{opendnp3::DoubleBitSpec::to_string(item.value.value)}),
                item.value.flags.value,
                item.value.time,
                Json{{"value_raw", opendnp3::DoubleBitSpec::to_type(item.value.value)}});
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Analog>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            Json extra = Json::object();
            auto value = finite_number(item.value.value, extra);
            operation_->record(
                info,
                item.index,
                "analog_input",
                std::move(value),
                item.value.flags.value,
                item.value.time,
                std::move(extra));
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Counter>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "counter",
                Json(item.value.value),
                item.value.flags.value,
                item.value.time);
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::FrozenCounter>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "frozen_counter",
                Json(item.value.value),
                item.value.flags.value,
                item.value.time);
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::BinaryOutputStatus>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "binary_output_status",
                Json(item.value.value),
                item.value.flags.value,
                item.value.time);
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::AnalogOutputStatus>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            Json extra = Json::object();
            auto value = finite_number(item.value.value, extra);
            operation_->record(
                info,
                item.index,
                "analog_output_status",
                std::move(value),
                item.value.flags.value,
                item.value.time,
                std::move(extra));
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::OctetString>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            const auto buffer = item.value.ToBuffer();
            operation_->record(
                info,
                item.index,
                "octet_string",
                Json(bytes_hex(buffer)),
                std::nullopt,
                opendnp3::DNPTime{},
                Json{{"encoding", "hex"}, {"length", buffer.length}});
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::TimeAndInterval>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            operation_->record(
                info,
                item.index,
                "time_and_interval",
                Json{{"time_ms", item.value.time.value},
                     {"interval", item.value.interval},
                     {"units", opendnp3::IntervalUnitsSpec::to_string(item.value.GetUnitsEnum())},
                     {"units_raw", item.value.units}},
                std::nullopt,
                item.value.time);
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::BinaryCommandEvent>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            const auto status_raw = static_cast<std::uint8_t>(
                opendnp3::CommandStatusSpec::to_type(item.value.status));
            operation_->record(
                info,
                item.index,
                "binary_command_event",
                Json(item.value.value),
                item.value.GetFlags().value,
                item.value.time,
                Json{{"command_status", std::string{command_status_name_2012(status_raw)}},
                     {"command_status_raw", status_raw},
                     {"command_status_edition", std::string{kTargetProtocolEdition}},
                     {"command_status_backend",
                      opendnp3::CommandStatusSpec::to_string(item.value.status)},
                     {"command_status_reserved_2012",
                      command_status_is_reserved_2012(status_raw)},
                     {"command_status_wire_raw_unambiguous",
                      command_status_wire_raw_unambiguous(status_raw)}});
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::AnalogCommandEvent>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            const auto status_raw = static_cast<std::uint8_t>(
                opendnp3::CommandStatusSpec::to_type(item.value.status));
            Json extra{
                {"command_status", std::string{command_status_name_2012(status_raw)}},
                {"command_status_raw", status_raw},
                {"command_status_edition", std::string{kTargetProtocolEdition}},
                {"command_status_backend",
                 opendnp3::CommandStatusSpec::to_string(item.value.status)},
                {"command_status_reserved_2012",
                 command_status_is_reserved_2012(status_raw)},
                {"command_status_wire_raw_unambiguous",
                 command_status_wire_raw_unambiguous(status_raw)}};
            auto value = finite_number(item.value.value, extra);
            operation_->record(
                info,
                item.index,
                "analog_command_event",
                std::move(value),
                std::nullopt,
                item.value.time,
                std::move(extra));
        });
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::DNPTime>& values) override
    {
        values.ForeachItem([this, &info](const auto& value) {
            operation_->record(
                info,
                std::nullopt,
                "time",
                Json(value.value),
                std::nullopt,
                value);
        });
    }

private:
    std::shared_ptr<ReadOperation> operation_;
};

opendnp3::Header make_header(const ReadHeader& header)
{
    switch (header.qualifier) {
    case ReadQualifier::AllObjects:
        return opendnp3::Header::AllObjects(header.group, header.variation);
    case ReadQualifier::Range8:
        return opendnp3::Header::Range8(
            header.group,
            header.variation,
            static_cast<std::uint8_t>(header.start),
            static_cast<std::uint8_t>(header.stop));
    case ReadQualifier::Range16:
        return opendnp3::Header::Range16(
            header.group, header.variation, header.start, header.stop);
    case ReadQualifier::Count8:
        return opendnp3::Header::Count8(
            header.group, header.variation, static_cast<std::uint8_t>(header.count));
    case ReadQualifier::Count16:
        return opendnp3::Header::Count16(
            header.group, header.variation, header.count);
    }
    return opendnp3::Header::AllObjects(header.group, header.variation);
}

}  // namespace

struct OpenDnp3ReadSupport::Impl final {
    explicit Impl(std::shared_ptr<MeasurementCapture> capture_value)
        : iin(std::make_shared<IinStore>()),
          completion_gate(std::make_shared<CompletionGate>()),
          receive_sequence(std::make_shared<std::atomic<std::uint64_t>>(0)),
          application(std::make_shared<TrackingMasterApplication>(iin, completion_gate)),
          capture(std::move(capture_value))
    {
    }

    template <class Submit>
    BackendOperationResult execute(
        const ReadOptions& options, const Submit& submit)
    {
        std::shared_ptr<ReadOperation> operation;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (active && !active->is_done()) {
                return BackendOperationResult::failure(
                    ErrorCode::InvalidState,
                    "a previous DNP3 read task is still active",
                    Json{{"active_task_id", active->task_id()}});
            }
            if (next_task_id == std::numeric_limits<int>::max()) {
                next_task_id = 0;
            }
            const auto task_id = ++next_task_id;
            operation = std::make_shared<ReadOperation>(
                task_id,
                options,
                receive_sequence,
                iin->last_sequence(),
                capture);
            active = operation;
            completion_gate->set_active(operation);
        }

        const auto handler = std::make_shared<CollectingSoeHandler>(operation);
        const auto callback = std::make_shared<ReadTaskCallback>(operation);
        const auto task_config = opendnp3::TaskConfig{
            opendnp3::TaskId::Defined(operation->task_id()), callback};
        try {
            submit(handler, task_config);
        }
        catch (const std::exception& error) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the read task submission",
                Json{{"task_id", operation->task_id()},
                     {"backend_message", error.what()}});
        }
        catch (...) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the read task submission",
                Json{{"task_id", operation->task_id()}});
        }

        operation->wait_for_completion();
        std::uint64_t iin_dropped = 0;
        std::uint64_t iin_window_dropped = 0;
        const auto observations = iin->after(
            operation->iin_start_sequence(), iin_dropped, iin_window_dropped);
        auto result = operation->outcome(
            observations, iin_dropped, iin_window_dropped);
        if (operation->is_done()) {
            clear_if_active(operation);
        }
        return result;
    }

    void clear_if_active(const std::shared_ptr<ReadOperation>& operation)
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (active == operation) {
            active.reset();
        }
        completion_gate->clear(operation);
    }

    void cancel_active() noexcept
    {
        std::shared_ptr<ReadOperation> operation;
        {
            std::lock_guard<std::mutex> lock(mutex);
            operation = std::move(active);
        }
        if (operation) {
            operation->cancel();
            completion_gate->clear(operation);
        }
    }

    std::mutex mutex;
    std::shared_ptr<IinStore> iin;
    std::shared_ptr<CompletionGate> completion_gate;
    std::shared_ptr<std::atomic<std::uint64_t>> receive_sequence;
    std::shared_ptr<TrackingMasterApplication> application;
    std::shared_ptr<MeasurementCapture> capture;
    std::shared_ptr<ReadOperation> active;
    int next_task_id{0};
};

OpenDnp3ReadSupport::OpenDnp3ReadSupport(
    std::shared_ptr<MeasurementCapture> capture)
    : impl_(std::make_shared<Impl>(std::move(capture)))
{
}

OpenDnp3ReadSupport::~OpenDnp3ReadSupport()
{
    cancel_active();
}

std::shared_ptr<opendnp3::IMasterApplication>
OpenDnp3ReadSupport::master_application() const
{
    return impl_->application;
}

BackendOperationResult OpenDnp3ReadSupport::integrity_poll(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const ReadOptions& options)
{
    return impl_->execute(
        options,
        [&master](
            const std::shared_ptr<opendnp3::ISOEHandler>& handler,
            const opendnp3::TaskConfig& task_config) {
            master->ScanClasses(
                opendnp3::ClassField::AllClasses(), handler, task_config);
        });
}

BackendOperationResult OpenDnp3ReadSupport::class_poll(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const ClassPollConfig& config)
{
    return impl_->execute(
        config.options,
        [&master, &config](
            const std::shared_ptr<opendnp3::ISOEHandler>& handler,
            const opendnp3::TaskConfig& task_config) {
            master->ScanClasses(
                opendnp3::ClassField{config.class_mask}, handler, task_config);
        });
}

BackendOperationResult OpenDnp3ReadSupport::read(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const ReadConfig& config)
{
    std::vector<opendnp3::Header> headers;
    headers.reserve(config.headers.size());
    std::transform(
        config.headers.begin(),
        config.headers.end(),
        std::back_inserter(headers),
        make_header);
    return impl_->execute(
        config.options,
        [&master, &headers](
            const std::shared_ptr<opendnp3::ISOEHandler>& handler,
            const opendnp3::TaskConfig& task_config) {
            master->Scan(headers, handler, task_config);
        });
}

void OpenDnp3ReadSupport::cancel_active() noexcept
{
    impl_->cancel_active();
}

}  // namespace dnp3host
