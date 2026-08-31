#include "dnp3host/OpenDnp3UnsolicitedSupport.h"
#include "dnp3host/Ieee1815_2012.h"
#include "dnp3host/MeasurementCapture.h"

#include <opendnp3/app/MeasurementTypes.h>
#include <opendnp3/app/OctetString.h>
#include <opendnp3/gen/CommandStatus.h>
#include <opendnp3/gen/DoubleBit.h>
#include <opendnp3/gen/FunctionCode.h>
#include <opendnp3/gen/GroupVariation.h>
#include <opendnp3/gen/IntervalUnits.h>
#include <opendnp3/gen/QualifierCode.h>
#include <opendnp3/gen/TaskCompletion.h>
#include <opendnp3/gen/TimestampQuality.h>
#include <opendnp3/master/HeaderInfo.h>
#include <opendnp3/master/HeaderTypes.h>
#include <opendnp3/master/IMaster.h>
#include <opendnp3/master/ISOEHandler.h>
#include <opendnp3/master/ITaskCallback.h>
#include <opendnp3/master/ResponseInfo.h>
#include <opendnp3/master/TaskConfig.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <exception>
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
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

Json class_list(const std::uint8_t mask)
{
    auto result = Json::array();
    for (std::uint8_t value = 1; value <= 3; ++value) {
        if ((mask & static_cast<std::uint8_t>(1U << value)) != 0U) {
            result.push_back(value);
        }
    }
    return result;
}

std::size_t validated_capacity(const std::size_t capacity)
{
    if (capacity == 0) {
        throw std::invalid_argument("unsolicited queue capacity must be positive");
    }
    return capacity;
}

class UnsolicitedStore final {
public:
    UnsolicitedStore(
        const std::size_t capacity,
        std::shared_ptr<MeasurementCapture> capture)
        : capacity_(capacity), capture_(std::move(capture))
    {
    }

    void begin_session(const std::uint64_t session_id)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        events_.clear();
        session_active_ = true;
        session_id_ = session_id;
        enabled_mask_ = 0;
        current_unsolicited_ = false;
        current_fragment_ = 0;
        last_sequence_ = 0;
        received_total_ = 0;
        dropped_total_ = 0;
        fragments_total_ = 0;
        by_kind_.clear();
        by_group_variation_.clear();
        condition_.notify_all();
    }

    void end_session() noexcept
    {
        std::lock_guard<std::mutex> lock(mutex_);
        events_.clear();
        session_active_ = false;
        session_id_ = 0;
        enabled_mask_ = 0;
        current_unsolicited_ = false;
        current_fragment_ = 0;
        condition_.notify_all();
    }

    void update_enabled(const std::uint8_t mask, const bool enabled)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!session_active_) {
            return;
        }
        enabled_mask_ = enabled
            ? static_cast<std::uint8_t>(enabled_mask_ | mask)
            : static_cast<std::uint8_t>(enabled_mask_ & ~mask);
    }

    void begin_fragment(const opendnp3::ResponseInfo& info)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        current_unsolicited_ = session_active_ && info.unsolicited;
        if (current_unsolicited_) {
            current_fragment_ = ++fragments_total_;
            if (capture_) {
                capture_->record_fragment("unsolicited", monotonic_ns());
            }
        }
    }

    void end_fragment(const opendnp3::ResponseInfo& info)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (info.unsolicited && current_unsolicited_) {
            condition_.notify_all();
        }
        current_unsolicited_ = false;
        current_fragment_ = 0;
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
        const auto encoded_gv = opendnp3::GroupVariationSpec::to_type(info.gv);
        const auto group = static_cast<std::uint8_t>((encoded_gv >> 8U) & 0xFFU);
        const auto variation = static_cast<std::uint8_t>(encoded_gv & 0xFFU);
        const auto received_at = monotonic_ns();

        std::lock_guard<std::mutex> lock(mutex_);
        if (!session_active_ || !current_unsolicited_) {
            return;
        }

        if (capture_) {
            capture_->record_object(
                "unsolicited",
                kind,
                group,
                variation,
                index,
                value,
                received_at);
        }

        const auto receive_sequence = ++last_sequence_;
        ++received_total_;
        ++by_kind_[kind];
        ++by_group_variation_[
            std::to_string(group) + ":" + std::to_string(variation)];
        Json record{
            {"receive_seq", receive_sequence},
            {"received_monotonic_ns", received_at},
            {"session_id", session_id_},
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
            {"source", "unsolicited"},
            {"fragment_index", current_fragment_}};
        for (auto iterator = extra.begin(); iterator != extra.end(); ++iterator) {
            record[iterator.key()] = iterator.value();
        }

        if (events_.size() == capacity_) {
            events_.pop_front();
            ++dropped_total_;
        }
        events_.push_back(std::move(record));
    }

    BackendOperationResult take(const WaitUnsolicitedConfig& config)
    {
        auto selected = Json::array();
        std::size_t remaining = 0;
        std::uint64_t dropped = 0;
        std::uint64_t received = 0;
        std::uint64_t last_sequence = 0;
        std::uint64_t fragments = 0;
        std::uint64_t session_id = 0;
        std::uint8_t enabled_mask = 0;
        bool session_active = false;
        bool timed_out = false;
        std::size_t returned = 0;
        Json by_kind = Json::object();
        Json by_group_variation = Json::object();
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if ((events_.empty() || current_unsolicited_)
                && config.timeout_ms > 0 && session_active_) {
                condition_.wait_for(
                    lock,
                    std::chrono::milliseconds{config.timeout_ms},
                    [this] {
                        return (!events_.empty() && !current_unsolicited_)
                            || !session_active_;
                    });
            }
            // Never expose a partially processed application fragment. Slow
            // instrumented builds can otherwise wake between two Process()
            // callbacks belonging to the same BeginFragment/EndFragment pair.
            const auto count = current_unsolicited_
                ? std::size_t{0}
                : std::min(config.max_events, events_.size());
            for (std::size_t index = 0; index < count; ++index) {
                selected.push_back(std::move(events_.front()));
                events_.pop_front();
            }
            timed_out = selected.empty();
            returned = count;
            remaining = events_.size();
            dropped = dropped_total_;
            received = received_total_;
            last_sequence = last_sequence_;
            fragments = fragments_total_;
            session_id = session_id_;
            enabled_mask = enabled_mask_;
            session_active = session_active_;
            for (const auto& entry : by_kind_) {
                by_kind[entry.first] = entry.second;
            }
            for (const auto& entry : by_group_variation_) {
                by_group_variation[entry.first] = entry.second;
            }
        }

        if (!session_active) {
            return BackendOperationResult::failure(
                ErrorCode::NotConnected,
                "no active DNP3 session is available for unsolicited events");
        }
        return BackendOperationResult::success(Json{
            {"session_id", session_id},
            {"enabled", enabled_mask != 0},
            {"classes", class_list(enabled_mask)},
            {"measurements", std::move(selected)},
            {"timed_out", timed_out},
            {"summary",
             Json{{"returned", returned},
                  {"remaining", remaining},
                  {"received_total", received},
                  {"dropped_total", dropped},
                  {"queue_capacity", capacity_},
                  {"last_receive_seq", last_sequence},
                  {"fragments_total", fragments},
                  {"by_kind", std::move(by_kind)},
                  {"by_group_variation", std::move(by_group_variation)}}}});
    }

    UnsolicitedSnapshot snapshot() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return UnsolicitedSnapshot{
            session_active_,
            enabled_mask_ != 0,
            enabled_mask_,
            last_sequence_,
            events_.size(),
            dropped_total_,
            fragments_total_};
    }

    std::size_t capacity() const noexcept { return capacity_; }

private:
    const std::size_t capacity_;
    std::shared_ptr<MeasurementCapture> capture_;
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::deque<Json> events_;
    bool session_active_{false};
    std::uint64_t session_id_{0};
    std::uint8_t enabled_mask_{0};
    bool current_unsolicited_{false};
    std::uint64_t current_fragment_{0};
    std::uint64_t last_sequence_{0};
    std::uint64_t received_total_{0};
    std::uint64_t dropped_total_{0};
    std::uint64_t fragments_total_{0};
    std::map<std::string, std::uint64_t> by_kind_;
    std::map<std::string, std::uint64_t> by_group_variation_;
};

class CollectingUnsolicitedHandler final : public opendnp3::ISOEHandler {
public:
    explicit CollectingUnsolicitedHandler(std::shared_ptr<UnsolicitedStore> store)
        : store_(std::move(store))
    {
    }

    void BeginFragment(const opendnp3::ResponseInfo& info) override
    {
        store_->begin_fragment(info);
    }

    void EndFragment(const opendnp3::ResponseInfo& info) override
    {
        store_->end_fragment(info);
    }

    void Process(
        const opendnp3::HeaderInfo& info,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Binary>>& values) override
    {
        values.ForeachItem([this, &info](const auto& item) {
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
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
            store_->record(
                info,
                std::nullopt,
                "time",
                Json(value.value),
                std::nullopt,
                value);
        });
    }

private:
    std::shared_ptr<UnsolicitedStore> store_;
};

class FunctionOperation final {
public:
    FunctionOperation(const int task_id, std::function<void()> on_success)
        : task_id_(task_id),
          submitted_monotonic_ns_(monotonic_ns()),
          on_success_(std::move(on_success))
    {
    }

    int task_id() const noexcept { return task_id_; }

    void mark_started()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        started_ = true;
        started_monotonic_ns_ = monotonic_ns();
    }

    void mark_complete(const opendnp3::TaskCompletion completion)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (cancelled_) {
            return;
        }
        if (completion == opendnp3::TaskCompletion::SUCCESS) {
            try {
                on_success_();
            }
            catch (...) {
                // Callback boundaries must not throw into OpenDNP3. A later
                // status/read makes any impossible bookkeeping failure visible.
            }
        }
        completion_ = completion;
        completed_monotonic_ns_ = monotonic_ns();
        done_ = true;
        condition_.notify_all();
    }

    void mark_destroyed()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        destroyed_ = true;
        if (!done_) {
            completed_monotonic_ns_ = monotonic_ns();
            done_ = true;
            condition_.notify_all();
        }
    }

    void cancel() noexcept
    {
        std::lock_guard<std::mutex> lock(mutex_);
        cancelled_ = true;
        completed_monotonic_ns_ = monotonic_ns();
        done_ = true;
        condition_.notify_all();
    }

    bool is_done() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return done_;
    }

    bool wait(const std::uint32_t timeout_ms)
    {
        std::unique_lock<std::mutex> lock(mutex_);
        return condition_.wait_for(
            lock,
            std::chrono::milliseconds{timeout_ms},
            [this] { return done_; });
    }

    BackendOperationResult outcome(
        const char* action,
        const UnsolicitedControlConfig& config) const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!done_) {
            return BackendOperationResult::failure(
                ErrorCode::ResponseTimeout,
                "DNP3 unsolicited control task did not complete before the configured deadline",
                Json{{"task_id", task_id_},
                     {"action", action},
                     {"timeout_ms", config.timeout_ms},
                     {"task_started", started_},
                     {"operation_may_complete_later", true},
                     {"automatic_retry_safe", false}});
        }
        if (cancelled_ || !completion_) {
            return BackendOperationResult::failure(
                ErrorCode::TaskFailed,
                "DNP3 unsolicited control task was destroyed before completion",
                Json{{"task_id", task_id_},
                     {"action", action},
                     {"task_started", started_},
                     {"task_destroyed", destroyed_}});
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
            {"action", action},
            {"classes", class_list(config.class_mask)},
            {"timings",
             Json{{"submitted_monotonic_ns", submitted_monotonic_ns_},
                  {"started_monotonic_ns",
                   started_monotonic_ns_ ? Json(*started_monotonic_ns_) : Json(nullptr)},
                  {"completed_monotonic_ns", completed_at},
                  {"duration_ms", static_cast<double>(duration_ns) / 1000000.0}}}};
        if (*completion_ == opendnp3::TaskCompletion::SUCCESS) {
            return BackendOperationResult::success(std::move(result));
        }
        const auto code = *completion_ == opendnp3::TaskCompletion::FAILURE_RESPONSE_TIMEOUT
            ? ErrorCode::ResponseTimeout
            : ErrorCode::TaskFailed;
        return BackendOperationResult::failure(
            code,
            "DNP3 unsolicited control task did not complete successfully",
            Json{{"task_id", task_id_},
                 {"action", action},
                 {"task_status", opendnp3::TaskCompletionSpec::to_string(*completion_)},
                 {"operation_result", std::move(result)}});
    }

private:
    const int task_id_;
    const std::uint64_t submitted_monotonic_ns_;
    const std::function<void()> on_success_;
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    bool started_{false};
    bool done_{false};
    bool destroyed_{false};
    bool cancelled_{false};
    std::optional<opendnp3::TaskCompletion> completion_;
    std::optional<std::uint64_t> started_monotonic_ns_;
    std::optional<std::uint64_t> completed_monotonic_ns_;
};

class FunctionTaskCallback final : public opendnp3::ITaskCallback {
public:
    explicit FunctionTaskCallback(std::shared_ptr<FunctionOperation> operation)
        : operation_(std::move(operation))
    {
    }

    void OnStart() override { operation_->mark_started(); }
    void OnComplete(const opendnp3::TaskCompletion result) override
    {
        operation_->mark_complete(result);
    }
    void OnDestroyed() override { operation_->mark_destroyed(); }

private:
    std::shared_ptr<FunctionOperation> operation_;
};

std::vector<opendnp3::Header> class_headers(const std::uint8_t mask)
{
    std::vector<opendnp3::Header> result;
    result.reserve(3);
    for (std::uint8_t value = 1; value <= 3; ++value) {
        if ((mask & static_cast<std::uint8_t>(1U << value)) != 0U) {
            result.push_back(opendnp3::Header::AllObjects(60, value + 1U));
        }
    }
    return result;
}

}  // namespace

struct OpenDnp3UnsolicitedSupport::Impl final {
    Impl(
        const std::size_t queue_capacity,
        std::shared_ptr<MeasurementCapture> capture)
        : store(std::make_shared<UnsolicitedStore>(
              queue_capacity, std::move(capture))),
          soe_handler(std::make_shared<CollectingUnsolicitedHandler>(store))
    {
    }

    BackendOperationResult execute(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const UnsolicitedControlConfig& config,
        const bool enable)
    {
        std::shared_ptr<FunctionOperation> operation;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (active && !active->is_done()) {
                return BackendOperationResult::failure(
                    ErrorCode::AlreadyExecuting,
                    "a previous unsolicited control task is still active",
                    Json{{"active_task_id", active->task_id()}});
            }
            active.reset();
            if (next_task_id == std::numeric_limits<int>::max()) {
                next_task_id = 0;
            }
            operation = std::make_shared<FunctionOperation>(
                ++next_task_id,
                [store = store, mask = config.class_mask, enable] {
                    store->update_enabled(mask, enable);
                });
            active = operation;
        }

        const auto action = enable ? "enable" : "disable";
        const auto callback = std::make_shared<FunctionTaskCallback>(operation);
        const auto task_config = opendnp3::TaskConfig{
            opendnp3::TaskId::Defined(operation->task_id()), callback};
        try {
            master->PerformFunction(
                enable ? "pytest enable unsolicited" : "pytest disable unsolicited",
                enable ? opendnp3::FunctionCode::ENABLE_UNSOLICITED
                       : opendnp3::FunctionCode::DISABLE_UNSOLICITED,
                class_headers(config.class_mask),
                task_config);
        }
        catch (const std::exception& error) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the unsolicited control task submission",
                Json{{"task_id", operation->task_id()},
                     {"action", action},
                     {"backend_message", error.what()}});
        }
        catch (...) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the unsolicited control task submission",
                Json{{"task_id", operation->task_id()}, {"action", action}});
        }

        operation->wait(config.timeout_ms);
        auto result = operation->outcome(action, config);
        if (operation->is_done()) {
            clear_if_active(operation);
        }
        return result;
    }

    void clear_if_active(const std::shared_ptr<FunctionOperation>& operation)
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (active == operation) {
            active.reset();
        }
    }

    void cancel_active() noexcept
    {
        std::shared_ptr<FunctionOperation> operation;
        {
            std::lock_guard<std::mutex> lock(mutex);
            operation = std::move(active);
        }
        if (operation) {
            operation->cancel();
        }
    }

    std::mutex mutex;
    std::shared_ptr<UnsolicitedStore> store;
    std::shared_ptr<CollectingUnsolicitedHandler> soe_handler;
    std::shared_ptr<FunctionOperation> active;
    int next_task_id{1000000};
};

OpenDnp3UnsolicitedSupport::OpenDnp3UnsolicitedSupport(
    const std::size_t queue_capacity,
    std::shared_ptr<MeasurementCapture> capture)
    : impl_(std::make_shared<Impl>(
          validated_capacity(queue_capacity), std::move(capture)))
{
}

OpenDnp3UnsolicitedSupport::~OpenDnp3UnsolicitedSupport()
{
    cancel_active();
    end_session();
}

std::shared_ptr<opendnp3::ISOEHandler>
OpenDnp3UnsolicitedSupport::handler() const
{
    return impl_->soe_handler;
}

std::size_t OpenDnp3UnsolicitedSupport::capacity() const noexcept
{
    return impl_->store->capacity();
}

void OpenDnp3UnsolicitedSupport::begin_session(const std::uint64_t session_id)
{
    impl_->store->begin_session(session_id);
}

void OpenDnp3UnsolicitedSupport::end_session() noexcept
{
    impl_->store->end_session();
}

UnsolicitedSnapshot OpenDnp3UnsolicitedSupport::snapshot() const
{
    return impl_->store->snapshot();
}

BackendOperationResult OpenDnp3UnsolicitedSupport::enable(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const UnsolicitedControlConfig& config)
{
    return impl_->execute(master, config, true);
}

BackendOperationResult OpenDnp3UnsolicitedSupport::disable(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const UnsolicitedControlConfig& config)
{
    return impl_->execute(master, config, false);
}

BackendOperationResult OpenDnp3UnsolicitedSupport::wait(
    const WaitUnsolicitedConfig& config)
{
    return impl_->store->take(config);
}

void OpenDnp3UnsolicitedSupport::cancel_active() noexcept
{
    impl_->cancel_active();
}

}  // namespace dnp3host
