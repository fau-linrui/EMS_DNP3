#include "dnp3host/OpenDnp3Backend.h"
#include "dnp3host/OpenDnp3CommandSupport.h"
#include "dnp3host/OpenDnp3ReadSupport.h"
#include "dnp3host/OpenDnp3UnsolicitedSupport.h"

#include <opendnp3/DNP3Manager.h>
#include <opendnp3/app/ClassField.h>
#include <opendnp3/channel/ChannelRetry.h>
#include <opendnp3/channel/IChannel.h>
#include <opendnp3/channel/IChannelListener.h>
#include <opendnp3/channel/IPEndpoint.h>
#include <opendnp3/gen/ChannelState.h>
#include <opendnp3/logging/LogLevels.h>
#include <opendnp3/master/IMaster.h>
#include <opendnp3/master/ISOEHandler.h>
#include <opendnp3/master/MasterStackConfig.h>
#include <opendnp3/util/TimeDuration.h>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <exception>
#include <memory>
#include <mutex>
#include <optional>
#include <random>
#include <string>
#include <utility>
#include <vector>

namespace dnp3host {
namespace {

constexpr std::size_t kEventQueueCapacity = 1024;

std::string generate_safety_token()
{
    static constexpr char digits[] = "0123456789abcdef";
    std::random_device source;
    std::string token(32, '0');
    for (std::size_t index = 0; index < token.size(); index += 2) {
        const auto value = static_cast<std::uint8_t>(source() & 0xFFU);
        token[index] = digits[(value >> 4U) & 0x0FU];
        token[index + 1U] = digits[value & 0x0FU];
    }
    return token;
}

const char* channel_state_name(const opendnp3::ChannelState state) noexcept
{
    switch (state) {
    case opendnp3::ChannelState::CLOSED:
        return "CLOSED";
    case opendnp3::ChannelState::OPENING:
        return "OPENING";
    case opendnp3::ChannelState::OPEN:
        return "OPEN";
    case opendnp3::ChannelState::SHUTDOWN:
        return "SHUTDOWN";
    }
    return "UNKNOWN";
}

struct ChannelEvent {
    std::uint64_t sequence{0};
    std::uint64_t session_id{0};
    std::uint64_t monotonic_ns{0};
    std::string state;
};

struct EventStoreSnapshot {
    std::string state{"CLOSED"};
    std::uint64_t session_id{0};
    std::uint64_t last_sequence{0};
    std::size_t queued_events{0};
    std::uint64_t dropped_events{0};
};

class ChannelEventStore final {
public:
    void begin_session(const std::uint64_t session_id)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        current_session_id_ = session_id;
        current_state_ = "CLOSED";
        condition_.notify_all();
    }

    void record(const std::uint64_t session_id, const opendnp3::ChannelState state)
    {
        const auto now = std::chrono::steady_clock::now().time_since_epoch();
        const auto monotonic_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());

        std::lock_guard<std::mutex> lock(mutex_);
        const auto state_name = std::string{channel_state_name(state)};
        if (session_id == current_session_id_) {
            current_state_ = state_name;
        }
        if (events_.size() == kEventQueueCapacity) {
            events_.pop_front();
            ++dropped_events_;
        }
        events_.push_back(ChannelEvent{
            ++last_sequence_, session_id, monotonic_ns, state_name});
        condition_.notify_all();
    }

    bool wait_until_open(
        const std::uint64_t session_id, const std::chrono::milliseconds timeout)
    {
        std::unique_lock<std::mutex> lock(mutex_);
        condition_.wait_for(lock, timeout, [this, session_id] {
            return current_session_id_ == session_id
                && (current_state_ == "OPEN" || current_state_ == "SHUTDOWN");
        });
        return current_session_id_ == session_id && current_state_ == "OPEN";
    }

    BackendOperationResult take(const WaitEventConfig& config)
    {
        std::vector<ChannelEvent> selected;
        std::size_t remaining = 0;
        std::uint64_t dropped = 0;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (events_.empty() && config.timeout_ms > 0) {
                condition_.wait_for(
                    lock,
                    std::chrono::milliseconds{config.timeout_ms},
                    [this] { return !events_.empty(); });
            }
            const auto count = std::min(config.max_events, events_.size());
            selected.reserve(count);
            for (std::size_t index = 0; index < count; ++index) {
                selected.push_back(std::move(events_.front()));
                events_.pop_front();
            }
            remaining = events_.size();
            dropped = dropped_events_;
        }

        auto serialized = Json::array();
        for (const auto& event : selected) {
            serialized.push_back(Json{
                {"sequence", event.sequence},
                {"session_id", event.session_id},
                {"type", "channel_state"},
                {"state", event.state},
                {"monotonic_ns", event.monotonic_ns}});
        }
        return BackendOperationResult::success(Json{
            {"events", std::move(serialized)},
            {"timed_out", selected.empty()},
            {"remaining", remaining},
            {"dropped_total", dropped}});
    }

    EventStoreSnapshot snapshot() const
    {
        std::lock_guard<std::mutex> lock(mutex_);
        return EventStoreSnapshot{
            current_state_,
            current_session_id_,
            last_sequence_,
            events_.size(),
            dropped_events_};
    }

private:
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::deque<ChannelEvent> events_;
    std::string current_state_{"CLOSED"};
    std::uint64_t current_session_id_{0};
    std::uint64_t last_sequence_{0};
    std::uint64_t dropped_events_{0};
};

class QueueingChannelListener final : public opendnp3::IChannelListener {
public:
    QueueingChannelListener(
        std::shared_ptr<ChannelEventStore> events, const std::uint64_t session_id)
        : events_(std::move(events)), session_id_(session_id)
    {
    }

    void OnStateChange(const opendnp3::ChannelState state) override
    {
        events_->record(session_id_, state);
    }

private:
    std::shared_ptr<ChannelEventStore> events_;
    std::uint64_t session_id_;
};

class NoOpSoeHandler final : public opendnp3::ISOEHandler {
public:
    void BeginFragment(const opendnp3::ResponseInfo&) override {}
    void EndFragment(const opendnp3::ResponseInfo&) override {}

    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Binary>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::DoubleBitBinary>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Analog>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::Counter>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::FrozenCounter>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::BinaryOutputStatus>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::AnalogOutputStatus>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::OctetString>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::TimeAndInterval>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::BinaryCommandEvent>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::Indexed<opendnp3::AnalogCommandEvent>>&) override
    {
    }
    void Process(
        const opendnp3::HeaderInfo&,
        const opendnp3::ICollection<opendnp3::DNPTime>&) override
    {
    }
};

struct BackendResources {
    std::unique_ptr<opendnp3::DNP3Manager> manager;
    std::shared_ptr<opendnp3::IChannel> channel;
    std::shared_ptr<opendnp3::IMaster> master;
};

std::optional<std::string> shutdown_resources(BackendResources resources) noexcept
{
    std::optional<std::string> first_error;
    const auto attempt = [&first_error](const auto& operation) {
        try {
            operation();
        }
        catch (const std::exception& error) {
            if (!first_error) {
                first_error = error.what();
            }
        }
        catch (...) {
            if (!first_error) {
                first_error = "unknown backend shutdown error";
            }
        }
    };

    if (resources.master) {
        attempt([&resources] { resources.master->Disable(); });
        attempt([&resources] { resources.master->Shutdown(); });
        resources.master.reset();
    }
    if (resources.channel) {
        attempt([&resources] { resources.channel->Shutdown(); });
        resources.channel.reset();
    }
    if (resources.manager) {
        attempt([&resources] { resources.manager->Shutdown(); });
        resources.manager.reset();
    }
    return first_error;
}

class OpenDnp3Backend final : public IMasterBackend {
public:
    explicit OpenDnp3Backend(const std::size_t unsolicited_queue_capacity)
        : events_(std::make_shared<ChannelEventStore>()),
          command_support_(std::make_unique<OpenDnp3CommandSupport>()),
          read_support_(std::make_unique<OpenDnp3ReadSupport>()),
          unsolicited_support_(std::make_unique<OpenDnp3UnsolicitedSupport>(
              unsolicited_queue_capacity))
    {
    }

    ~OpenDnp3Backend() override
    {
        shutdown();
    }

    std::string name() const override
    {
        return "opendnp3";
    }

    std::string version() const override
    {
        return "3.1.2";
    }

    std::vector<std::string> supported_commands() const override
    {
        return {
            "class_poll",
            "connect",
            "direct_operate",
            "disable_unsolicited",
            "disconnect",
            "enable_unsolicited",
            "get_status",
            "hello",
            "integrity_poll",
            "read",
            "select_and_operate",
            "shutdown",
            "stats",
            "wait_event",
            "wait_unsolicited"};
    }

    Json capabilities() const override
    {
        const auto channel_entry = Json{
            {"status", "IMPLEMENTED_UNVERIFIED"},
            {"implementation_revision", "t05-opendnp3-3.1.2"}};
        const auto read_entry = Json{
            {"status", "IMPLEMENTED_UNVERIFIED"},
            {"implementation_revision", "t06-t08-opendnp3-3.1.2"},
            {"verification_scope", "local_opendnp3_outstation"}};
        const auto command_entry = Json{
            {"status", "IMPLEMENTED_UNVERIFIED"},
            {"implementation_revision", "t09-t11-opendnp3-3.1.2"},
            {"verification_scope", "local_opendnp3_outstation"},
            {"requires_safety_interlock", true}};
        const auto unsolicited_entry = Json{
            {"status", "IMPLEMENTED_UNVERIFIED"},
            {"implementation_revision", "t12-opendnp3-3.1.2"},
            {"verification_scope", "local_opendnp3_outstation"},
            {"queue_capacity", unsolicited_support_->capacity()}};
        return Json{
            {"CHANNEL.TCP.CLIENT", channel_entry},
            {"CHANNEL.RECONNECT", channel_entry},
            {"APP.FC.01.READ", read_entry},
            {"APP.TASK.LIFECYCLE", read_entry},
            {"APP.TASK.OBSERVABILITY", read_entry},
            {"APP.CLASS.EVENTS", read_entry},
            {"APP.UNSOLICITED", unsolicited_entry},
            {"APP.FC.14.ENABLE_UNSOLICITED", unsolicited_entry},
            {"APP.FC.15.DISABLE_UNSOLICITED", unsolicited_entry},
            {"APP.FC.82.UNSOLICITED_RESPONSE", unsolicited_entry},
            {"APP.FC.03.SELECT", command_entry},
            {"APP.FC.04.OPERATE", command_entry},
            {"APP.FC.05.DIRECT_OPERATE", command_entry},
            {"APP.COMMAND_STATUS.CATALOG", command_entry},
            {"IIN.IIN2.1.OBJECT_UNKNOWN", read_entry},
            {"OBJ.G12.V1", command_entry},
            {"OBJ.G1.V2", read_entry},
            {"OBJ.G2.V2", read_entry},
            {"OBJ.G3.V2", read_entry},
            {"OBJ.G10.V2", read_entry},
            {"OBJ.G20.V1", read_entry},
            {"OBJ.G21.V1", read_entry},
            {"OBJ.G30.V5", read_entry},
            {"OBJ.G32.V7", read_entry},
            {"OBJ.G40.V1", read_entry},
            {"OBJ.G40.V3", read_entry},
            {"OBJ.G41.V1", command_entry},
            {"OBJ.G41.V2", command_entry},
            {"OBJ.G41.V3", command_entry},
            {"OBJ.G41.V4", command_entry},
            {"OBJ.G50.V4", read_entry},
            {"OBJ.G60.V1", read_entry},
            {"OBJ.G60.V2", read_entry},
            {"OBJ.G60.V3", read_entry},
            {"OBJ.G60.V4", read_entry},
            {"OBJ.G110.LENGTH_VARIANTS", read_entry}};
    }

    BackendStatus status() const override
    {
        bool active = false;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            active = session_active_;
        }
        const auto snapshot = events_->snapshot();
        const auto unsolicited = unsolicited_support_->snapshot();
        bool state_change_authorized = false;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            state_change_authorized = !safety_token_.empty();
        }
        return BackendStatus{
            active,
            snapshot.state,
            snapshot.session_id,
            snapshot.last_sequence,
            snapshot.queued_events,
            snapshot.dropped_events,
            state_change_authorized,
            unsolicited.enabled,
            unsolicited.class_mask,
            unsolicited.last_sequence,
            unsolicited.queued_events,
            unsolicited.dropped_events,
            unsolicited.fragments};
    }

    BackendOperationResult connect(const ConnectionConfig& config) override
    {
        std::uint64_t session_id = 0;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            if (shutting_down_) {
                return BackendOperationResult::failure(
                    ErrorCode::ProcessShuttingDown,
                    "backend is shutting down");
            }
            if (session_active_) {
                return BackendOperationResult::failure(
                    ErrorCode::AlreadyConnected,
                    "a DNP3 channel session is already active",
                    Json{{"session_id", current_session_id_}});
            }
            session_id = ++next_session_id_;
        }
        events_->begin_session(session_id);
        unsolicited_support_->begin_session(session_id);

        BackendResources created;
        std::string safety_token;
        try {
            if (config.allow_state_change && config.safety_environment == "LAB"
                && !config.operator_id.empty() && !config.dut_id.empty()) {
                safety_token = generate_safety_token();
            }
            created.manager = std::make_unique<opendnp3::DNP3Manager>(1);
            const auto listener = std::make_shared<QueueingChannelListener>(events_, session_id);
            const auto retry = opendnp3::ChannelRetry{
                opendnp3::TimeDuration::Milliseconds(config.retry_min_ms),
                opendnp3::TimeDuration::Milliseconds(config.retry_max_ms)};
            created.channel = created.manager->AddTCPClient(
                "pytest-tcp-client",
                opendnp3::levels::NOTHING,
                retry,
                {opendnp3::IPEndpoint{config.host, config.port}},
                config.local_adapter,
                listener);

            opendnp3::MasterStackConfig stack_config;
            stack_config.link.LocalAddr = config.master_address;
            stack_config.link.RemoteAddr = config.outstation_address;
            stack_config.link.KeepAliveTimeout =
                opendnp3::TimeDuration::Milliseconds(config.keep_alive_timeout_ms);
            stack_config.master.disableUnsolOnStartup = false;
            stack_config.master.unsolClassMask = opendnp3::ClassField::None();
            stack_config.master.startupIntegrityClassMask = opendnp3::ClassField::None();
            stack_config.master.eventScanOnEventsAvailableClassMask =
                opendnp3::ClassField::None();
            stack_config.master.integrityOnEventOverflowIIN = false;

            created.master = created.channel->AddMaster(
                "pytest-master",
                unsolicited_support_->handler(),
                read_support_->master_application(),
                stack_config);
        }
        catch (const std::exception& error) {
            shutdown_resources(std::move(created));
            unsolicited_support_->end_session();
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "failed to create the OpenDNP3 TCP channel",
                Json{{"reason", "backend_setup"}, {"backend_message", error.what()}});
        }

        const auto master = created.master;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            manager_ = std::move(created.manager);
            channel_ = std::move(created.channel);
            master_ = std::move(created.master);
            session_active_ = true;
            current_session_id_ = session_id;
            safety_token_ = safety_token;
        }

        bool enabled = false;
        try {
            enabled = master->Enable();
        }
        catch (const std::exception& error) {
            shutdown_resources(detach_resources());
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 failed while enabling the master",
                Json{{"reason", "enable_exception"},
                     {"session_id", session_id},
                     {"backend_message", error.what()}});
        }
        catch (...) {
            shutdown_resources(detach_resources());
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 failed while enabling the master",
                Json{{"reason", "enable_exception"}, {"session_id", session_id}});
        }
        if (!enabled) {
            shutdown_resources(detach_resources());
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "OpenDNP3 rejected the master enable request",
                Json{{"reason", "enable_failed"}, {"session_id", session_id}});
        }

        const auto opened = events_->wait_until_open(
            session_id, std::chrono::milliseconds{config.connect_timeout_ms});
        if (!opened) {
            const auto before_cleanup = events_->snapshot();
            shutdown_resources(detach_resources());
            return BackendOperationResult::failure(
                ErrorCode::ConnectionTimeout,
                "TCP connection did not open before the configured deadline",
                Json{{"host", config.host},
                     {"port", config.port},
                     {"connect_timeout_ms", config.connect_timeout_ms},
                     {"last_channel_state", before_cleanup.state},
                     {"session_id", session_id}});
        }

        return BackendOperationResult::success(Json{
            {"state", "CONNECTED"},
            {"channel_state", "OPEN"},
            {"session_id", session_id},
            {"endpoint", Json{{"host", config.host}, {"port", config.port}}},
            {"safety",
             Json{{"environment", config.safety_environment},
                  {"state_change_authorized", !safety_token.empty()},
                  {"safety_token",
                   safety_token.empty() ? Json(nullptr) : Json(safety_token)},
                  {"operator_id",
                   config.operator_id.empty() ? Json(nullptr) : Json(config.operator_id)},
                  {"dut_id", config.dut_id.empty() ? Json(nullptr) : Json(config.dut_id)},
                  {"expires_on", "disconnect_or_process_exit"}}}});
    }

    BackendOperationResult disconnect() override
    {
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            if (!session_active_) {
                return BackendOperationResult::failure(
                    ErrorCode::NotConnected,
                    "no DNP3 channel session is active");
            }
        }

        const auto session_id = status().session_id;
        command_support_->cancel_active();
        read_support_->cancel_active();
        unsolicited_support_->cancel_active();
        if (const auto cleanup_error = shutdown_resources(detach_resources())) {
            return BackendOperationResult::failure(
                ErrorCode::InternalError,
                "the DNP3 channel closed with a cleanup error",
                Json{{"session_id", session_id}, {"backend_message", *cleanup_error}});
        }
        const auto snapshot = events_->snapshot();
        return BackendOperationResult::success(Json{
            {"state", "READY"},
            {"channel_state", snapshot.state},
            {"session_id", session_id}});
    }

    BackendOperationResult integrity_poll(const ReadOptions& options) override
    {
        const auto master = master_for_read();
        if (!master) {
            return read_unavailable();
        }
        return read_support_->integrity_poll(master, options);
    }

    BackendOperationResult class_poll(const ClassPollConfig& config) override
    {
        const auto master = master_for_read();
        if (!master) {
            return read_unavailable();
        }
        return read_support_->class_poll(master, config);
    }

    BackendOperationResult read(const ReadConfig& config) override
    {
        const auto master = master_for_read();
        if (!master) {
            return read_unavailable();
        }
        return read_support_->read(master, config);
    }

    BackendOperationResult enable_unsolicited(
        const UnsolicitedControlConfig& config) override
    {
        const auto master = master_for_read();
        if (!master) {
            return unsolicited_unavailable("enable");
        }
        return unsolicited_support_->enable(master, config);
    }

    BackendOperationResult disable_unsolicited(
        const UnsolicitedControlConfig& config) override
    {
        const auto master = master_for_read();
        if (!master) {
            return unsolicited_unavailable("disable");
        }
        return unsolicited_support_->disable(master, config);
    }

    BackendOperationResult wait_unsolicited(
        const WaitUnsolicitedConfig& config) override
    {
        return unsolicited_support_->wait(config);
    }

    BackendOperationResult select_and_operate(const CommandConfig& config) override
    {
        std::shared_ptr<opendnp3::IMaster> master;
        if (const auto error = command_access(config.safety_token, master)) {
            return BackendOperationResult::failure(
                error->code, error->message, error->details);
        }
        return command_support_->select_and_operate(master, config);
    }

    BackendOperationResult direct_operate(const CommandConfig& config) override
    {
        std::shared_ptr<opendnp3::IMaster> master;
        if (const auto error = command_access(config.safety_token, master)) {
            return BackendOperationResult::failure(
                error->code, error->message, error->details);
        }
        return command_support_->direct_operate(master, config);
    }

    BackendOperationResult wait_event(const WaitEventConfig& config) override
    {
        return events_->take(config);
    }

    void shutdown() noexcept override
    {
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            if (shutting_down_) {
                return;
            }
            shutting_down_ = true;
        }
        command_support_->cancel_active();
        read_support_->cancel_active();
        unsolicited_support_->cancel_active();
        shutdown_resources(detach_resources());
    }

private:
    std::optional<BackendError> command_access(
        const std::string& token,
        std::shared_ptr<opendnp3::IMaster>& master) const
    {
        std::lock_guard<std::mutex> lock(resources_mutex_);
        if (!session_active_ || !master_) {
            return BackendError{
                ErrorCode::NotConnected,
                "no active DNP3 master session is available for a command task",
                Json::object()};
        }
        if (safety_token_.empty()) {
            return BackendError{
                ErrorCode::SafetyInterlock,
                "state-changing DNP3 commands are locked for this session",
                Json{{"reason", "session_not_authorized"},
                     {"required_environment", "LAB"},
                     {"automatic_retry_safe", false}}};
        }
        if (token != safety_token_) {
            return BackendError{
                ErrorCode::SafetyInterlock,
                "state-changing DNP3 command safety token is invalid or expired",
                Json{{"reason", "invalid_or_expired_token"},
                     {"automatic_retry_safe", false}}};
        }
        const auto channel = events_->snapshot();
        if (channel.state != "OPEN") {
            return BackendError{
                ErrorCode::InvalidState,
                "state-changing DNP3 commands require an open channel",
                Json{{"reason", "channel_not_open"},
                     {"channel_state", channel.state},
                     {"session_id", channel.session_id},
                     {"automatic_retry_safe", false}}};
        }
        master = master_;
        return std::nullopt;
    }

    std::shared_ptr<opendnp3::IMaster> master_for_read() const
    {
        std::lock_guard<std::mutex> lock(resources_mutex_);
        if (!session_active_) {
            return nullptr;
        }
        return master_;
    }

    BackendOperationResult read_unavailable() const
    {
        const auto snapshot = events_->snapshot();
        return BackendOperationResult::failure(
            ErrorCode::NotConnected,
            "no active DNP3 master session is available for a read task",
            Json{{"channel_state", snapshot.state},
                 {"session_id", snapshot.session_id}});
    }

    BackendOperationResult unsolicited_unavailable(const char* action) const
    {
        const auto snapshot = events_->snapshot();
        return BackendOperationResult::failure(
            ErrorCode::NotConnected,
            "no active DNP3 master session is available for unsolicited control",
            Json{{"action", action},
                 {"channel_state", snapshot.state},
                 {"session_id", snapshot.session_id}});
    }

    BackendResources detach_resources() noexcept
    {
        BackendResources detached;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            detached = BackendResources{
                std::move(manager_), std::move(channel_), std::move(master_)};
            session_active_ = false;
            safety_token_.clear();
        }
        unsolicited_support_->end_session();
        return detached;
    }

    std::shared_ptr<ChannelEventStore> events_;
    std::unique_ptr<OpenDnp3CommandSupport> command_support_;
    std::unique_ptr<OpenDnp3ReadSupport> read_support_;
    std::unique_ptr<OpenDnp3UnsolicitedSupport> unsolicited_support_;
    mutable std::mutex resources_mutex_;
    std::unique_ptr<opendnp3::DNP3Manager> manager_;
    std::shared_ptr<opendnp3::IChannel> channel_;
    std::shared_ptr<opendnp3::IMaster> master_;
    bool session_active_{false};
    bool shutting_down_{false};
    std::uint64_t next_session_id_{0};
    std::uint64_t current_session_id_{0};
    std::string safety_token_;
};

}  // namespace

std::unique_ptr<IMasterBackend> make_opendnp3_backend(
    const std::size_t unsolicited_queue_capacity)
{
    return std::make_unique<OpenDnp3Backend>(unsolicited_queue_capacity);
}

}  // namespace dnp3host
