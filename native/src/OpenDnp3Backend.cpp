#include "dnp3host/OpenDnp3Backend.h"

#include <opendnp3/DNP3Manager.h>
#include <opendnp3/app/ClassField.h>
#include <opendnp3/channel/ChannelRetry.h>
#include <opendnp3/channel/IChannel.h>
#include <opendnp3/channel/IChannelListener.h>
#include <opendnp3/channel/IPEndpoint.h>
#include <opendnp3/gen/ChannelState.h>
#include <opendnp3/logging/LogLevels.h>
#include <opendnp3/master/DefaultMasterApplication.h>
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
#include <string>
#include <utility>
#include <vector>

namespace dnp3host {
namespace {

constexpr std::size_t kEventQueueCapacity = 1024;

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
    OpenDnp3Backend() : events_(std::make_shared<ChannelEventStore>()) {}

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
        return {"connect", "disconnect", "get_status", "hello", "shutdown", "wait_event"};
    }

    Json capabilities() const override
    {
        const auto entry = Json{
            {"status", "IMPLEMENTED_UNVERIFIED"},
            {"implementation_revision", "t05-opendnp3-3.1.2"}};
        return Json{
            {"CHANNEL.TCP.CLIENT", entry},
            {"CHANNEL.RECONNECT", entry}};
    }

    BackendStatus status() const override
    {
        bool active = false;
        {
            std::lock_guard<std::mutex> lock(resources_mutex_);
            active = session_active_;
        }
        const auto snapshot = events_->snapshot();
        return BackendStatus{
            active,
            snapshot.state,
            snapshot.session_id,
            snapshot.last_sequence,
            snapshot.queued_events,
            snapshot.dropped_events};
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

        BackendResources created;
        try {
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
                std::make_shared<NoOpSoeHandler>(),
                opendnp3::DefaultMasterApplication::Create(),
                stack_config);
        }
        catch (const std::exception& error) {
            shutdown_resources(std::move(created));
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
            {"endpoint", Json{{"host", config.host}, {"port", config.port}}}});
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
        shutdown_resources(detach_resources());
    }

private:
    BackendResources detach_resources() noexcept
    {
        std::lock_guard<std::mutex> lock(resources_mutex_);
        BackendResources detached{
            std::move(manager_), std::move(channel_), std::move(master_)};
        session_active_ = false;
        return detached;
    }

    std::shared_ptr<ChannelEventStore> events_;
    mutable std::mutex resources_mutex_;
    std::unique_ptr<opendnp3::DNP3Manager> manager_;
    std::shared_ptr<opendnp3::IChannel> channel_;
    std::shared_ptr<opendnp3::IMaster> master_;
    bool session_active_{false};
    bool shutting_down_{false};
    std::uint64_t next_session_id_{0};
    std::uint64_t current_session_id_{0};
};

}  // namespace

std::unique_ptr<IMasterBackend> make_opendnp3_backend()
{
    return std::make_unique<OpenDnp3Backend>();
}

}  // namespace dnp3host
