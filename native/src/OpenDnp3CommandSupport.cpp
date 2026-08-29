#include "dnp3host/OpenDnp3CommandSupport.h"

#include <opendnp3/app/AnalogOutput.h>
#include <opendnp3/app/ControlRelayOutputBlock.h>
#include <opendnp3/app/Indexed.h>
#include <opendnp3/gen/CommandPointState.h>
#include <opendnp3/gen/CommandStatus.h>
#include <opendnp3/gen/OperationType.h>
#include <opendnp3/gen/TaskCompletion.h>
#include <opendnp3/gen/TripCloseCode.h>
#include <opendnp3/master/CommandSet.h>
#include <opendnp3/master/ICommandTaskResult.h>
#include <opendnp3/master/IMaster.h>
#include <opendnp3/master/ITaskCallback.h>
#include <opendnp3/master/TaskConfig.h>

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <exception>
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

std::uint64_t monotonic_ns() noexcept
{
    const auto duration = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count());
}

const char* command_kind_name(const CommandKind kind) noexcept
{
    switch (kind) {
    case CommandKind::Crob:
        return "crob";
    case CommandKind::AnalogOutputInt16:
        return "analog_output_int16";
    case CommandKind::AnalogOutputInt32:
        return "analog_output_int32";
    case CommandKind::AnalogOutputFloat32:
        return "analog_output_float32";
    case CommandKind::AnalogOutputDouble64:
        return "analog_output_double64";
    }
    return "unknown";
}

const char* crob_operation_name(const CrobOperation operation) noexcept
{
    switch (operation) {
    case CrobOperation::Null:
        return "null";
    case CrobOperation::PulseOn:
        return "pulse_on";
    case CrobOperation::PulseOff:
        return "pulse_off";
    case CrobOperation::LatchOn:
        return "latch_on";
    case CrobOperation::LatchOff:
        return "latch_off";
    }
    return "unknown";
}

const char* trip_close_name(const TripCloseSelection value) noexcept
{
    switch (value) {
    case TripCloseSelection::Null:
        return "null";
    case TripCloseSelection::Close:
        return "close";
    case TripCloseSelection::Trip:
        return "trip";
    }
    return "unknown";
}

opendnp3::OperationType operation_type(const CrobOperation operation) noexcept
{
    switch (operation) {
    case CrobOperation::Null:
        return opendnp3::OperationType::NUL;
    case CrobOperation::PulseOn:
        return opendnp3::OperationType::PULSE_ON;
    case CrobOperation::PulseOff:
        return opendnp3::OperationType::PULSE_OFF;
    case CrobOperation::LatchOn:
        return opendnp3::OperationType::LATCH_ON;
    case CrobOperation::LatchOff:
        return opendnp3::OperationType::LATCH_OFF;
    }
    return opendnp3::OperationType::Undefined;
}

opendnp3::TripCloseCode trip_close_code(const TripCloseSelection value) noexcept
{
    switch (value) {
    case TripCloseSelection::Null:
        return opendnp3::TripCloseCode::NUL;
    case TripCloseSelection::Close:
        return opendnp3::TripCloseCode::CLOSE;
    case TripCloseSelection::Trip:
        return opendnp3::TripCloseCode::TRIP;
    }
    return opendnp3::TripCloseCode::NUL;
}

std::uint64_t point_key(const std::uint32_t header_index, const std::uint16_t index)
{
    return (static_cast<std::uint64_t>(header_index) << 16U) | index;
}

struct BuiltCommands {
    opendnp3::CommandSet command_set;
    std::map<std::uint64_t, Json> metadata;
    std::size_t expected_points{0};
};

Json requested_point_json(const CommandPoint& point, const std::size_t ordinal)
{
    Json requested{
        {"request_ordinal", ordinal},
        {"type", command_kind_name(point.kind)},
        {"index", point.index}};
    switch (point.kind) {
    case CommandKind::Crob:
        requested["group"] = 12;
        requested["variation"] = 1;
        requested["operation"] = crob_operation_name(point.crob_operation);
        requested["trip_close"] = trip_close_name(point.trip_close);
        requested["clear"] = point.clear;
        requested["count"] = point.count;
        requested["on_time_ms"] = point.on_time_ms;
        requested["off_time_ms"] = point.off_time_ms;
        break;
    case CommandKind::AnalogOutputInt16:
        requested["group"] = 41;
        requested["variation"] = 2;
        requested["value"] = point.integer_value;
        break;
    case CommandKind::AnalogOutputInt32:
        requested["group"] = 41;
        requested["variation"] = 1;
        requested["value"] = point.integer_value;
        break;
    case CommandKind::AnalogOutputFloat32:
        requested["group"] = 41;
        requested["variation"] = 3;
        requested["value"] = static_cast<float>(point.floating_value);
        break;
    case CommandKind::AnalogOutputDouble64:
        requested["group"] = 41;
        requested["variation"] = 4;
        requested["value"] = point.floating_value;
        break;
    }
    return requested;
}

BuiltCommands build_command_set(const CommandConfig& config)
{
    BuiltCommands built;
    std::vector<opendnp3::Indexed<opendnp3::ControlRelayOutputBlock>> crobs;
    std::vector<opendnp3::Indexed<opendnp3::AnalogOutputInt16>> int16_values;
    std::vector<opendnp3::Indexed<opendnp3::AnalogOutputInt32>> int32_values;
    std::vector<opendnp3::Indexed<opendnp3::AnalogOutputFloat32>> float32_values;
    std::vector<opendnp3::Indexed<opendnp3::AnalogOutputDouble64>> double64_values;
    std::map<CommandKind, std::vector<std::pair<std::size_t, const CommandPoint*>>> grouped;

    for (std::size_t ordinal = 0; ordinal < config.commands.size(); ++ordinal) {
        grouped[config.commands[ordinal].kind].push_back(
            std::make_pair(ordinal, &config.commands[ordinal]));
    }

    std::uint32_t header_index = 0;
    const auto add_metadata = [&built, &header_index](
                                  const auto& entries) {
        for (const auto& entry : entries) {
            built.metadata.emplace(
                point_key(header_index, entry.second->index),
                requested_point_json(*entry.second, entry.first));
        }
        ++header_index;
    };

    const auto& crob_entries = grouped[CommandKind::Crob];
    if (!crob_entries.empty()) {
        crobs.reserve(crob_entries.size());
        for (const auto& entry : crob_entries) {
            const auto& point = *entry.second;
            crobs.push_back(opendnp3::WithIndex(
                opendnp3::ControlRelayOutputBlock{
                    operation_type(point.crob_operation),
                    trip_close_code(point.trip_close),
                    point.clear,
                    point.count,
                    point.on_time_ms,
                    point.off_time_ms},
                point.index));
        }
        built.command_set.Add(std::move(crobs));
        add_metadata(crob_entries);
    }

    const auto& int16_entries = grouped[CommandKind::AnalogOutputInt16];
    if (!int16_entries.empty()) {
        int16_values.reserve(int16_entries.size());
        for (const auto& entry : int16_entries) {
            int16_values.push_back(opendnp3::WithIndex(
                opendnp3::AnalogOutputInt16{
                    static_cast<std::int16_t>(entry.second->integer_value)},
                entry.second->index));
        }
        built.command_set.Add(std::move(int16_values));
        add_metadata(int16_entries);
    }

    const auto& int32_entries = grouped[CommandKind::AnalogOutputInt32];
    if (!int32_entries.empty()) {
        int32_values.reserve(int32_entries.size());
        for (const auto& entry : int32_entries) {
            int32_values.push_back(opendnp3::WithIndex(
                opendnp3::AnalogOutputInt32{
                    static_cast<std::int32_t>(entry.second->integer_value)},
                entry.second->index));
        }
        built.command_set.Add(std::move(int32_values));
        add_metadata(int32_entries);
    }

    const auto& float32_entries = grouped[CommandKind::AnalogOutputFloat32];
    if (!float32_entries.empty()) {
        float32_values.reserve(float32_entries.size());
        for (const auto& entry : float32_entries) {
            float32_values.push_back(opendnp3::WithIndex(
                opendnp3::AnalogOutputFloat32{
                    static_cast<float>(entry.second->floating_value)},
                entry.second->index));
        }
        built.command_set.Add(std::move(float32_values));
        add_metadata(float32_entries);
    }

    const auto& double64_entries = grouped[CommandKind::AnalogOutputDouble64];
    if (!double64_entries.empty()) {
        double64_values.reserve(double64_entries.size());
        for (const auto& entry : double64_entries) {
            double64_values.push_back(opendnp3::WithIndex(
                opendnp3::AnalogOutputDouble64{entry.second->floating_value},
                entry.second->index));
        }
        built.command_set.Add(std::move(double64_values));
        add_metadata(double64_entries);
    }
    built.expected_points = config.commands.size();
    return built;
}

class CommandOperation final {
public:
    CommandOperation(
        const int task_id,
        std::string mode,
        const std::uint32_t timeout_ms,
        std::map<std::uint64_t, Json> metadata,
        const std::size_t expected_points)
        : task_id_(task_id),
          mode_(std::move(mode)),
          timeout_ms_(timeout_ms),
          metadata_(std::move(metadata)),
          expected_points_(expected_points),
          submitted_monotonic_ns_(monotonic_ns())
    {
    }

    int task_id() const noexcept
    {
        return task_id_;
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

    void mark_task_complete(const opendnp3::TaskCompletion completion)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        task_callback_completion_ = completion;
    }

    void mark_destroyed()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        destroyed_ = true;
        if (!done_ && task_callback_completion_) {
            task_completion_ = *task_callback_completion_;
            completed_monotonic_ns_ = monotonic_ns();
            done_ = true;
        }
        condition_.notify_all();
    }

    void complete(const opendnp3::ICommandTaskResult& result)
    {
        auto serialized = Json::array();
        std::size_t success_count = 0;
        std::map<std::string, std::size_t> by_status;
        std::map<std::string, std::size_t> by_state;
        result.ForeachItem([&](const opendnp3::CommandPointResult& point) {
            const auto state_name = std::string{
                opendnp3::CommandPointStateSpec::to_string(point.state)};
            const auto status_name = std::string{
                opendnp3::CommandStatusSpec::to_string(point.status)};
            ++by_state[state_name];
            ++by_status[status_name];
            if (point.state == opendnp3::CommandPointState::SUCCESS
                && point.status == opendnp3::CommandStatus::SUCCESS) {
                ++success_count;
            }
            Json item{
                {"header_index", point.headerIndex},
                {"index", point.index},
                {"state", state_name},
                {"state_raw", opendnp3::CommandPointStateSpec::to_type(point.state)},
                {"status", status_name},
                {"status_raw", opendnp3::CommandStatusSpec::to_type(point.status)}};
            const auto metadata = metadata_.find(point_key(point.headerIndex, point.index));
            item["requested"] = metadata == metadata_.end()
                ? Json(nullptr)
                : metadata->second;
            serialized.push_back(std::move(item));
        });

        auto statuses = Json::object();
        for (const auto& entry : by_status) {
            statuses[entry.first] = entry.second;
        }
        auto states = Json::object();
        for (const auto& entry : by_state) {
            states[entry.first] = entry.second;
        }

        std::lock_guard<std::mutex> lock(mutex_);
        if (done_) {
            return;
        }
        task_completion_ = result.summary;
        point_results_ = std::move(serialized);
        success_count_ = success_count;
        by_status_ = std::move(statuses);
        by_state_ = std::move(states);
        completed_monotonic_ns_ = monotonic_ns();
        done_ = true;
        condition_.notify_all();
    }

    void cancel() noexcept
    {
        try {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!done_) {
                cancelled_ = true;
                destroyed_ = true;
                task_completion_ = opendnp3::TaskCompletion::FAILURE_NO_COMMS;
                completed_monotonic_ns_ = monotonic_ns();
                done_ = true;
            }
            condition_.notify_all();
        }
        catch (...) {
        }
    }

    bool wait_for_completion()
    {
        std::unique_lock<std::mutex> lock(mutex_);
        return condition_.wait_for(
            lock,
            std::chrono::milliseconds{timeout_ms_},
            [this] { return done_; });
    }

    BackendOperationResult outcome() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!done_ || !task_completion_) {
            return BackendOperationResult::failure(
                ErrorCode::ResponseTimeout,
                "DNP3 command task did not complete before the configured deadline",
                Json{{"task_id", task_id_},
                     {"timeout_ms", timeout_ms_},
                     {"task_started", started_},
                     {"execution_uncertain", true},
                     {"may_still_execute", true},
                     {"automatic_retry_safe", false}});
        }

        const auto returned_points = point_results_.size();
        const auto all_points_success = returned_points == expected_points_
            && success_count_ == expected_points_;
        const auto task_failed =
            *task_completion_ != opendnp3::TaskCompletion::SUCCESS || cancelled_;
        const auto execution_uncertain = started_ && task_failed;
        const auto all_success = *task_completion_ == opendnp3::TaskCompletion::SUCCESS
            && all_points_success && !cancelled_;
        const auto completed_at = completed_monotonic_ns_.value_or(monotonic_ns());
        const auto duration_ns = completed_at >= submitted_monotonic_ns_
            ? completed_at - submitted_monotonic_ns_
            : 0U;
        Json result{
            {"task_id", task_id_},
            {"mode", mode_},
            {"response_mode", "response"},
            {"task_status", opendnp3::TaskCompletionSpec::to_string(*task_completion_)},
            {"task_callback_status",
             task_callback_completion_
                 ? Json(opendnp3::TaskCompletionSpec::to_string(*task_callback_completion_))
                 : Json(nullptr)},
            {"task_started", started_},
            {"task_destroyed", destroyed_},
            {"all_success", all_success},
            {"execution_uncertain", execution_uncertain},
            {"point_results", point_results_},
            {"summary",
             Json{{"requested_points", expected_points_},
                  {"returned_points", returned_points},
                  {"successful_points", success_count_},
                  {"failed_points", returned_points - success_count_},
                  {"by_status", by_status_},
                  {"by_state", by_state_}}},
            {"timings",
             Json{{"submitted_monotonic_ns", submitted_monotonic_ns_},
                  {"started_monotonic_ns",
                   started_monotonic_ns_ ? Json(*started_monotonic_ns_) : Json(nullptr)},
                  {"completed_monotonic_ns", completed_at},
                  {"duration_ms", static_cast<double>(duration_ns) / 1000000.0}}}};

        if (task_failed) {
            const auto error_code =
                *task_completion_
                        == opendnp3::TaskCompletion::FAILURE_RESPONSE_TIMEOUT
                    ? ErrorCode::ResponseTimeout
                    : ErrorCode::CommandFailed;
            return BackendOperationResult::failure(
                error_code,
                "DNP3 command task did not complete successfully",
                Json{{"task_id", task_id_},
                     {"task_status", opendnp3::TaskCompletionSpec::to_string(*task_completion_)},
                     {"execution_uncertain", execution_uncertain},
                     {"may_still_execute", execution_uncertain},
                     {"operation_result", std::move(result)},
                     {"automatic_retry_safe", false}});
        }
        if (returned_points != expected_points_) {
            result["execution_uncertain"] = true;
            return BackendOperationResult::failure(
                ErrorCode::ProtocolError,
                "DNP3 command result did not contain every requested point",
                Json{{"task_id", task_id_},
                     {"requested_points", expected_points_},
                     {"returned_points", returned_points},
                     {"execution_uncertain", true},
                     {"may_still_execute", true},
                     {"operation_result", std::move(result)},
                     {"automatic_retry_safe", false}});
        }
        return BackendOperationResult::success(std::move(result));
    }

private:
    const int task_id_;
    const std::string mode_;
    const std::uint32_t timeout_ms_;
    const std::map<std::uint64_t, Json> metadata_;
    const std::size_t expected_points_;
    const std::uint64_t submitted_monotonic_ns_;

    mutable std::mutex mutex_;
    std::condition_variable condition_;
    bool started_{false};
    bool destroyed_{false};
    bool cancelled_{false};
    bool done_{false};
    std::optional<opendnp3::TaskCompletion> task_callback_completion_;
    std::optional<opendnp3::TaskCompletion> task_completion_;
    std::optional<std::uint64_t> started_monotonic_ns_;
    std::optional<std::uint64_t> completed_monotonic_ns_;
    Json point_results_{Json::array()};
    std::size_t success_count_{0};
    Json by_status_{Json::object()};
    Json by_state_{Json::object()};
};

class CommandTaskCallback final : public opendnp3::ITaskCallback {
public:
    explicit CommandTaskCallback(std::shared_ptr<CommandOperation> operation)
        : operation_(std::move(operation))
    {
    }

    void OnStart() override
    {
        operation_->mark_started();
    }

    void OnComplete(const opendnp3::TaskCompletion result) override
    {
        operation_->mark_task_complete(result);
    }

    void OnDestroyed() override
    {
        operation_->mark_destroyed();
    }

private:
    std::shared_ptr<CommandOperation> operation_;
};

}  // namespace

struct OpenDnp3CommandSupport::Impl final {
    BackendOperationResult execute(
        const std::shared_ptr<opendnp3::IMaster>& master,
        const CommandConfig& config,
        const bool select_before_operate)
    {
        if (config.no_response) {
            return BackendOperationResult::failure(
                ErrorCode::UnsupportedByBackend,
                "OpenDNP3 3.1.2 public master API does not expose Direct Operate No Response",
                Json{{"response_mode", "no_response"},
                     {"backend", "opendnp3"},
                     {"backend_version", "3.1.2"}});
        }

        std::shared_ptr<CommandOperation> operation;
        std::unique_ptr<BuiltCommands> built;
        try {
            built = std::make_unique<BuiltCommands>(build_command_set(config));
        }
        catch (const std::exception& error) {
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "failed to build the OpenDNP3 command set",
                Json{{"backend_message", error.what()}});
        }
        catch (...) {
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "failed to build the OpenDNP3 command set");
        }

        {
            std::lock_guard<std::mutex> lock(mutex);
            if (active && !active->is_done()) {
                return BackendOperationResult::failure(
                    ErrorCode::AlreadyExecuting,
                    "a previous state-changing DNP3 command may still be executing",
                    Json{{"active_task_id", active->task_id()},
                         {"automatic_retry_safe", false}});
            }
            active.reset();
            if (next_task_id == std::numeric_limits<int>::max()) {
                next_task_id = 0;
            }
            operation = std::make_shared<CommandOperation>(
                ++next_task_id,
                select_before_operate ? "select_and_operate" : "direct_operate",
                config.timeout_ms,
                std::move(built->metadata),
                built->expected_points);
            active = operation;
        }

        const auto task_callback = std::make_shared<CommandTaskCallback>(operation);
        const auto task_config = opendnp3::TaskConfig{
            opendnp3::TaskId::Defined(operation->task_id()), task_callback};
        const auto result_callback = [operation](
                                         const opendnp3::ICommandTaskResult& result) {
            operation->complete(result);
        };
        try {
            if (select_before_operate) {
                master->SelectAndOperate(
                    std::move(built->command_set), result_callback, task_config);
            }
            else {
                master->DirectOperate(
                    std::move(built->command_set), result_callback, task_config);
            }
        }
        catch (const std::exception& error) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the command task submission",
                Json{{"task_id", operation->task_id()},
                     {"backend_message", error.what()},
                     {"automatic_retry_safe", false}});
        }
        catch (...) {
            operation->cancel();
            clear_if_active(operation);
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the command task submission",
                Json{{"task_id", operation->task_id()},
                     {"automatic_retry_safe", false}});
        }

        operation->wait_for_completion();
        auto result = operation->outcome();
        if (operation->is_done()) {
            clear_if_active(operation);
        }
        return result;
    }

    void clear_if_active(const std::shared_ptr<CommandOperation>& operation)
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (active == operation) {
            active.reset();
        }
    }

    void cancel_active() noexcept
    {
        std::shared_ptr<CommandOperation> operation;
        {
            std::lock_guard<std::mutex> lock(mutex);
            operation = std::move(active);
        }
        if (operation) {
            operation->cancel();
        }
    }

    std::mutex mutex;
    std::shared_ptr<CommandOperation> active;
    int next_task_id{0};
};

OpenDnp3CommandSupport::OpenDnp3CommandSupport()
    : impl_(std::make_shared<Impl>())
{
}

OpenDnp3CommandSupport::~OpenDnp3CommandSupport()
{
    cancel_active();
}

BackendOperationResult OpenDnp3CommandSupport::select_and_operate(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const CommandConfig& config)
{
    return impl_->execute(master, config, true);
}

BackendOperationResult OpenDnp3CommandSupport::direct_operate(
    const std::shared_ptr<opendnp3::IMaster>& master,
    const CommandConfig& config)
{
    return impl_->execute(master, config, false);
}

void OpenDnp3CommandSupport::cancel_active() noexcept
{
    impl_->cancel_active();
}

}  // namespace dnp3host
