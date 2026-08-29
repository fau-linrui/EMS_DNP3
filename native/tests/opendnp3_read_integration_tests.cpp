#include "dnp3host/OpenDnp3Backend.h"

#include <opendnp3/DNP3Manager.h>
#include <opendnp3/app/MeasurementTypes.h>
#include <opendnp3/app/OctetString.h>
#include <opendnp3/channel/IChannel.h>
#include <opendnp3/channel/IPEndpoint.h>
#include <opendnp3/gen/CommandStatus.h>
#include <opendnp3/gen/EventAnalogVariation.h>
#include <opendnp3/gen/EventBinaryVariation.h>
#include <opendnp3/gen/PointClass.h>
#include <opendnp3/gen/ServerAcceptMode.h>
#include <opendnp3/gen/StaticAnalogOutputStatusVariation.h>
#include <opendnp3/gen/StaticAnalogVariation.h>
#include <opendnp3/gen/StaticBinaryOutputStatusVariation.h>
#include <opendnp3/gen/StaticBinaryVariation.h>
#include <opendnp3/logging/LogLevels.h>
#include <opendnp3/outstation/DefaultOutstationApplication.h>
#include <opendnp3/outstation/EventBufferConfig.h>
#include <opendnp3/outstation/IOutstation.h>
#include <opendnp3/outstation/OutstationStackConfig.h>
#include <opendnp3/outstation/SimpleCommandHandler.h>
#include <opendnp3/outstation/UpdateBuilder.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <set>
#include <string>
#include <string_view>
#include <thread>

namespace {

constexpr std::uint32_t kLocalTaskTimeoutMs = 15000;

constexpr const char* TC_APP_FC01_INTEGRITY_LOCAL_001 =
    "TC_APP_FC01_INTEGRITY_LOCAL_001";
constexpr const char* TC_APP_FC01_MULTI_HEADER_LOCAL_001 =
    "TC_APP_FC01_MULTI_HEADER_LOCAL_001";
constexpr const char* TC_APP_TASK_COMPLETION_LOCAL_001 =
    "TC_APP_TASK_COMPLETION_LOCAL_001";
constexpr const char* TC_APP_IIN_OBJECT_UNKNOWN_LOCAL_001 =
    "TC_APP_IIN_OBJECT_UNKNOWN_LOCAL_001";
constexpr const char* TC_APP_MEASUREMENT_TYPES_LOCAL_001 =
    "TC_APP_MEASUREMENT_TYPES_LOCAL_001";
constexpr const char* TC_APP_MEASUREMENT_OVERFLOW_LOCAL_001 =
    "TC_APP_MEASUREMENT_OVERFLOW_LOCAL_001";
constexpr const char* TC_APP_CLASS_POLL_LOCAL_001 =
    "TC_APP_CLASS_POLL_LOCAL_001";
constexpr const char* TC_APP_EMS_PROFILE_VARIATIONS_LOCAL_001 =
    "TC_APP_EMS_PROFILE_VARIATIONS_LOCAL_001";
constexpr const char* TC_APP_UNSOLICITED_LOCAL_001 =
    "TC_APP_UNSOLICITED_LOCAL_001";
constexpr const char* TC_APP_UNSOLICITED_DISABLE_LOCAL_001 =
    "TC_APP_UNSOLICITED_DISABLE_LOCAL_001";
constexpr const char* TC_APP_UNSOLICITED_OVERFLOW_LOCAL_001 =
    "TC_APP_UNSOLICITED_OVERFLOW_LOCAL_001";
constexpr const char* TC_APP_CROB_SBO_LOCAL_001 =
    "TC_APP_CROB_SBO_LOCAL_001";
constexpr const char* TC_APP_DIRECT_OPERATE_LOCAL_001 =
    "TC_APP_DIRECT_OPERATE_LOCAL_001";
constexpr const char* TC_APP_ANALOG_OUTPUT_BATCH_LOCAL_001 =
    "TC_APP_ANALOG_OUTPUT_BATCH_LOCAL_001";
constexpr const char* TC_APP_COMMAND_STATUS_LOCAL_001 =
    "TC_APP_COMMAND_STATUS_LOCAL_001";
constexpr const char* TC_APP_CONTROL_SAFETY_LOCAL_001 =
    "TC_APP_CONTROL_SAFETY_LOCAL_001";
constexpr const char* TC_APP_CONTROL_CHANNEL_GATE_LOCAL_001 =
    "TC_APP_CONTROL_CHANNEL_GATE_LOCAL_001";
constexpr const char* TC_APP_DIRECT_OPERATE_NR_GAP_001 =
    "TC_APP_DIRECT_OPERATE_NR_GAP_001";

int failures = 0;

void check(const bool condition, const std::string_view message)
{
    if (!condition) {
        ++failures;
        std::cerr << "FAILED: " << message << '\n';
    }
}

class LocalOutstation final {
public:
    explicit LocalOutstation(
        std::shared_ptr<opendnp3::ICommandHandler> command_handler =
            opendnp3::SuccessCommandHandler::Create())
        : manager_(1)
    {
        const auto seed = static_cast<std::uint32_t>(
            std::chrono::steady_clock::now().time_since_epoch().count());
        for (std::uint32_t attempt = 0; attempt < 64; ++attempt) {
            const auto candidate = static_cast<std::uint16_t>(
                22000U + ((seed + attempt * 7919U) % 35000U));
            try {
                channel_ = manager_.AddTCPServer(
                    "local-read-test-server",
                    opendnp3::levels::NOTHING,
                    opendnp3::ServerAcceptMode::CloseExisting,
                    opendnp3::IPEndpoint{"127.0.0.1", candidate},
                    nullptr);
                port_ = candidate;
                break;
            }
            catch (const std::exception&) {
            }
        }
        if (!channel_) {
            throw std::runtime_error("unable to reserve a local DNP3 test port");
        }

        opendnp3::DatabaseConfig database(2);
        database.binary_input[0].clazz = opendnp3::PointClass::Class1;
        database.binary_input[0].svariation =
            opendnp3::StaticBinaryVariation::Group1Var2;
        database.binary_input[0].evariation =
            opendnp3::EventBinaryVariation::Group2Var2;
        database.analog_input[0].clazz = opendnp3::PointClass::Class2;
        database.analog_input[0].svariation =
            opendnp3::StaticAnalogVariation::Group30Var5;
        database.analog_input[0].evariation =
            opendnp3::EventAnalogVariation::Group32Var7;
        database.binary_output_status[0].svariation =
            opendnp3::StaticBinaryOutputStatusVariation::Group10Var2;
        database.analog_output_status[0].svariation =
            opendnp3::StaticAnalogOutputStatusVariation::Group40Var3;
        opendnp3::OutstationStackConfig config(database);
        config.outstation.eventBufferConfig = opendnp3::EventBufferConfig::AllTypes(32);
        config.outstation.params.allowUnsolicited = true;
        config.link.LocalAddr = 1024;
        config.link.RemoteAddr = 1;

        outstation_ = channel_->AddOutstation(
            "local-read-test-outstation",
            std::move(command_handler),
            opendnp3::DefaultOutstationApplication::Create(),
            config);
        if (!outstation_ || !outstation_->Enable()) {
            throw std::runtime_error("unable to enable the local DNP3 test outstation");
        }

        const std::uint8_t octets[] = {0xDE, 0xAD, 0xBE, 0xEF};
        opendnp3::UpdateBuilder updates;
        updates.Update(
            opendnp3::Binary{
                true, opendnp3::Flags{0x01}, opendnp3::DNPTime{1700000000001ULL}},
            0);
        updates.Update(
            opendnp3::DoubleBitBinary{
                opendnp3::DoubleBit::DETERMINED_ON,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000002ULL}},
            0);
        updates.Update(
            opendnp3::Analog{
                123.5,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000004ULL}},
            0);
        updates.Update(
            opendnp3::Counter{42, opendnp3::Flags{0x01}}, 0);
        updates.FreezeCounter(0, false);
        updates.Update(
            opendnp3::BinaryOutputStatus{true, opendnp3::Flags{0x01}}, 0);
        updates.Update(
            opendnp3::AnalogOutputStatus{-12.25, opendnp3::Flags{0x01}}, 0);
        updates.Update(
            opendnp3::OctetString{opendnp3::Buffer{octets, sizeof(octets)}}, 0);
        updates.Update(
            opendnp3::TimeAndInterval{
                opendnp3::DNPTime{1700000000003ULL},
                15,
                opendnp3::IntervalUnits::Seconds},
            0);
        outstation_->Apply(updates.Build());
    }

    ~LocalOutstation()
    {
        stop();
        manager_.Shutdown();
    }

    void stop()
    {
        if (outstation_) {
            outstation_->Disable();
            outstation_->Shutdown();
            outstation_.reset();
        }
        if (channel_) {
            channel_->Shutdown();
            channel_.reset();
        }
    }

    std::uint16_t port() const noexcept
    {
        return port_;
    }

    void generate_profile_events()
    {
        opendnp3::UpdateBuilder updates;
        updates.Update(
            opendnp3::Binary{
                false,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000101ULL}},
            0,
            opendnp3::EventMode::Force);
        updates.Update(
            opendnp3::Analog{
                456.25,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000102ULL}},
            0,
            opendnp3::EventMode::Force);
        outstation_->Apply(updates.Build());
    }

private:
    opendnp3::DNP3Manager manager_;
    std::shared_ptr<opendnp3::IChannel> channel_;
    std::shared_ptr<opendnp3::IOutstation> outstation_;
    std::uint16_t port_{0};
};

dnp3host::ConnectionConfig connection_config(const std::uint16_t port)
{
    dnp3host::ConnectionConfig config;
    config.host = "127.0.0.1";
    config.port = port;
    config.connect_timeout_ms = 3000;
    config.retry_min_ms = 50;
    config.retry_max_ms = 200;
    config.master_address = 1;
    config.outstation_address = 1024;
    return config;
}

dnp3host::ConnectionConfig authorized_connection_config(const std::uint16_t port)
{
    auto config = connection_config(port);
    config.allow_state_change = true;
    config.safety_environment = "LAB";
    config.operator_id = "local-test-operator";
    config.dut_id = "local-opendnp3-outstation";
    return config;
}

void check_successful_task(const dnp3host::BackendOperationResult& operation)
{
    check(!operation.error.has_value(), "local DNP3 task must succeed");
    if (operation.error) {
        std::cerr << operation.error->message << ": " << operation.error->details.dump()
                  << '\n';
        return;
    }
    check(
        operation.result.value("task_status", "") == "SUCCESS",
        "task callback must report SUCCESS");
    check(operation.result.value("task_started", false), "task callback must report start");
    check(
        operation.result.at("timings").at("duration_ms").get<double>() >= 0.0,
        "task duration must be non-negative");
}

void run_read_integration()
{
    check(TC_APP_FC01_INTEGRITY_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_FC01_MULTI_HEADER_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_TASK_COMPLETION_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_IIN_OBJECT_UNKNOWN_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_MEASUREMENT_TYPES_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_MEASUREMENT_OVERFLOW_LOCAL_001 != nullptr, "stable test ID must exist");
    check(TC_APP_CLASS_POLL_LOCAL_001 != nullptr, "stable class-poll test ID must exist");
    check(
        TC_APP_EMS_PROFILE_VARIATIONS_LOCAL_001 != nullptr,
        "stable EMS-profile variation test ID must exist");
    check(
        TC_APP_UNSOLICITED_LOCAL_001 != nullptr,
        "stable unsolicited test ID must exist");
    check(
        TC_APP_UNSOLICITED_DISABLE_LOCAL_001 != nullptr,
        "stable unsolicited-disable test ID must exist");

    LocalOutstation outstation;
    auto backend = dnp3host::make_opendnp3_backend();

    dnp3host::ReadOptions default_options;
    // MSVC AddressSanitizer and endpoint security can make the first local
    // protocol exchange substantially slower than warm Release/Debug runs.
    default_options.timeout_ms = kLocalTaskTimeoutMs;
    const auto not_connected = backend->integrity_poll(default_options);
    check(not_connected.error.has_value(), "read before connect must fail");
    if (not_connected.error) {
        check(
            not_connected.error->code == dnp3host::ErrorCode::NotConnected,
            "read before connect must use NOT_CONNECTED");
    }

    const auto connected = backend->connect(connection_config(outstation.port()));
    check(!connected.error.has_value(), "master must connect to the local outstation");
    if (connected.error) {
        std::cerr << connected.error->message << ": " << connected.error->details.dump()
                  << '\n';
        return;
    }

    const auto integrity = backend->integrity_poll(default_options);
    check_successful_task(integrity);
    if (!integrity.error) {
        const auto& measurements = integrity.result.at("measurements");
        std::set<std::string> kinds;
        for (const auto& measurement : measurements) {
            kinds.insert(measurement.at("kind").get<std::string>());
            check(
                measurement.at("receive_seq").is_number_unsigned(),
                "measurement receive sequence must be an unsigned integer");
            check(
                measurement.at("flags_raw").is_null()
                    || measurement.at("flags_raw").is_number_integer()
                    || measurement.at("flags_raw").is_number_unsigned(),
                "measurement flags must preserve their raw octet or be null");
        }
        const auto has_profile_measurement = [&measurements](
                                                 const std::string_view kind,
                                                 const int group,
                                                 const int variation,
                                                 const bool is_event) {
            return std::any_of(
                measurements.begin(),
                measurements.end(),
                [kind, group, variation, is_event](const auto& measurement) {
                    return measurement.at("kind").template get<std::string>() == kind
                        && measurement.at("group") == group
                        && measurement.at("variation") == variation
                        && measurement.at("is_event") == is_event;
                });
        };
        check(
            has_profile_measurement("binary_input", 1, 2, false),
            "integrity response must include EMS-profile G1V2");
        check(
            has_profile_measurement("analog_input", 30, 5, false),
            "integrity response must include EMS-profile G30V5");
        check(
            has_profile_measurement("binary_output_status", 10, 2, false),
            "integrity response must include EMS-profile G10V2");
        check(
            has_profile_measurement("analog_output_status", 40, 3, false),
            "integrity response must include EMS-profile G40V3");
        check(
            has_profile_measurement("binary_input", 2, 2, true),
            "all-classes integrity response must include buffered G2V2");
        check(
            has_profile_measurement("analog_input", 32, 7, true),
            "all-classes integrity response must include buffered G32V7");
        for (const auto* expected : {
                 "binary_input",
                 "double_bit_binary_input",
                 "analog_input",
                 "counter",
                 "frozen_counter",
                 "binary_output_status",
                 "analog_output_status",
                 "octet_string",
                 "time_and_interval"}) {
            check(kinds.count(expected) != 0, "integrity response must include every public static type");
        }
        check(
            integrity.result.at("summary").at("received_total").get<std::uint64_t>()
                >= kinds.size(),
            "summary count must cover all detailed measurements");
    }

    dnp3host::ReadConfig analog_read;
    analog_read.options.timeout_ms = kLocalTaskTimeoutMs;
    analog_read.headers.push_back(dnp3host::ReadHeader{
        30, 0, dnp3host::ReadQualifier::Range16, 0, 0, 0});
    const auto analog = backend->read(analog_read);
    check_successful_task(analog);
    if (!analog.error) {
        check(
            analog.result.at("measurements").size() == 1,
            "single-point analog range read must return one value");
        const auto& measurement = analog.result.at("measurements").front();
        check(measurement.at("kind") == "analog_input", "range read type must be analog");
        check(measurement.at("index") == 0, "range read must preserve the point index");
        if (measurement.at("value") != 123.5) {
            std::cerr << "Analog diagnostic: " << measurement.dump() << '\n';
        }
        check(measurement.at("value") == 123.5, "range read must preserve the analog value");
    }

    dnp3host::ReadConfig multi_read;
    multi_read.options.timeout_ms = kLocalTaskTimeoutMs;
    multi_read.options.return_mode = dnp3host::ReturnMode::Summary;
    multi_read.headers = {
        dnp3host::ReadHeader{
            1, 0, dnp3host::ReadQualifier::AllObjects, 0, 0, 0},
        dnp3host::ReadHeader{
            20, 0, dnp3host::ReadQualifier::AllObjects, 0, 0, 0}};
    const auto multi = backend->read(multi_read);
    check_successful_task(multi);
    if (!multi.error) {
        check(
            multi.result.at("measurements").empty(),
            "summary mode must not serialize per-point details");
        check(
            multi.result.at("summary").at("received_total").get<std::uint64_t>() >= 4,
            "multi-header summary must count both object families");
    }

    outstation.generate_profile_events();
    dnp3host::ClassPollConfig class_poll;
    class_poll.options.timeout_ms = kLocalTaskTimeoutMs;
    class_poll.class_mask = 0x06;
    const auto events = backend->class_poll(class_poll);
    check_successful_task(events);
    if (!events.error) {
        bool found_binary_event = false;
        bool found_analog_event = false;
        for (const auto& measurement : events.result.at("measurements")) {
            check(
                measurement.at("source") == "solicited",
                "Class Read events must be identified as solicited");
            if (measurement.at("group") == 2 && measurement.at("variation") == 2
                && measurement.at("index") == 0) {
                found_binary_event = true;
                check(measurement.at("is_event") == true, "G2V2 must be an event");
                check(
                    measurement.at("dnp3_timestamp_ms") == 1700000000101ULL,
                    "G2V2 must preserve absolute time");
            }
            if (measurement.at("group") == 32 && measurement.at("variation") == 7
                && measurement.at("index") == 0) {
                found_analog_event = true;
                check(measurement.at("is_event") == true, "G32V7 must be an event");
                check(
                    measurement.at("dnp3_timestamp_ms") == 1700000000102ULL,
                    "G32V7 must preserve absolute time");
                check(
                    measurement.at("value") == 456.25,
                    "G32V7 must preserve the floating-point value");
            }
        }
        check(found_binary_event, "Class 1 Read must return EMS-profile G2V2");
        check(found_analog_event, "Class 2 Read must return EMS-profile G32V7");
    }

    dnp3host::ReadConfig class_headers;
    class_headers.options.timeout_ms = kLocalTaskTimeoutMs;
    class_headers.options.return_mode = dnp3host::ReturnMode::Summary;
    class_headers.headers = {
        dnp3host::ReadHeader{
            60, 1, dnp3host::ReadQualifier::AllObjects, 0, 0, 0},
        dnp3host::ReadHeader{
            60, 2, dnp3host::ReadQualifier::AllObjects, 0, 0, 0},
        dnp3host::ReadHeader{
            60, 3, dnp3host::ReadQualifier::AllObjects, 0, 0, 0},
        dnp3host::ReadHeader{
            60, 4, dnp3host::ReadQualifier::AllObjects, 0, 0, 0}};
    const auto class_header_result = backend->read(class_headers);
    check_successful_task(class_header_result);

    dnp3host::UnsolicitedControlConfig unsolicited_control;
    unsolicited_control.timeout_ms = kLocalTaskTimeoutMs;
    unsolicited_control.class_mask = 0x06;
    const auto enabled_unsolicited =
        backend->enable_unsolicited(unsolicited_control);
    check_successful_task(enabled_unsolicited);
    if (!enabled_unsolicited.error) {
        check(
            enabled_unsolicited.result.at("action") == "enable",
            "unsolicited control must report enable action");
        check(
            enabled_unsolicited.result.at("classes")
                == dnp3host::Json::array({1, 2}),
            "unsolicited control must preserve selected classes");
    }

    outstation.generate_profile_events();
    dnp3host::WaitUnsolicitedConfig wait_unsolicited;
    wait_unsolicited.timeout_ms = kLocalTaskTimeoutMs;
    wait_unsolicited.max_events = 16;
    const auto unsolicited = backend->wait_unsolicited(wait_unsolicited);
    check(!unsolicited.error.has_value(), "unsolicited event wait must succeed");
    if (!unsolicited.error) {
        bool found_binary_event = false;
        bool found_analog_event = false;
        for (const auto& measurement : unsolicited.result.at("measurements")) {
            check(
                measurement.at("source") == "unsolicited",
                "persistent SOE records must identify unsolicited source");
            check(
                measurement.at("session_id") == connected.result.at("session_id"),
                "unsolicited records must preserve the DNP3 session ID");
            if (measurement.at("group") == 2
                && measurement.at("variation") == 2
                && measurement.at("index") == 0) {
                found_binary_event = true;
            }
            if (measurement.at("group") == 32
                && measurement.at("variation") == 7
                && measurement.at("index") == 0) {
                found_analog_event = true;
                check(
                    measurement.at("value") == 456.25,
                    "unsolicited G32V7 must preserve its floating-point value");
            }
        }
        check(found_binary_event, "unsolicited collector must receive G2V2");
        check(found_analog_event, "unsolicited collector must receive G32V7");
        check(
            unsolicited.result.at("summary").at("queue_capacity") == 4096,
            "unsolicited collector must expose its fixed bounded capacity");
        check(
            unsolicited.result.at("summary").at("fragments_total") >= 1,
            "unsolicited collector must count response fragments");
    }

    const auto disabled_unsolicited =
        backend->disable_unsolicited(unsolicited_control);
    check_successful_task(disabled_unsolicited);
    if (!disabled_unsolicited.error) {
        check(
            disabled_unsolicited.result.at("action") == "disable",
            "unsolicited control must report disable action");
    }
    check(
        !backend->status().unsolicited_enabled,
        "successful disable must clear the advertised unsolicited state");

    outstation.generate_profile_events();
    wait_unsolicited.timeout_ms = 300;
    const auto after_disable = backend->wait_unsolicited(wait_unsolicited);
    check(!after_disable.error.has_value(), "disabled unsolicited wait must succeed");
    if (!after_disable.error) {
        check(
            after_disable.result.at("measurements").empty(),
            "events generated after disable must not enter the unsolicited queue");
        check(
            after_disable.result.at("timed_out") == true,
            "empty disabled unsolicited wait must report timeout");
    }

    dnp3host::ReadConfig unknown;
    unknown.options.timeout_ms = kLocalTaskTimeoutMs;
    unknown.headers.push_back(dnp3host::ReadHeader{
        199, 1, dnp3host::ReadQualifier::AllObjects, 0, 0, 0});
    const auto unknown_result = backend->read(unknown);
    check_successful_task(unknown_result);
    if (!unknown_result.error) {
        bool found = false;
        for (const auto& bit : unknown_result.result.at("iin").at("bits")) {
            if (bit == "IIN2.1.OBJECT_UNKNOWN") {
                found = true;
            }
        }
        if (!found) {
            std::cerr << "IIN diagnostic: "
                      << unknown_result.result.at("iin").dump() << '\n';
        }
        check(found, "unknown object read must expose parsed IIN2.1");
        check(
            unknown_result.result.at("iin").at("raw_hex").is_string(),
            "IIN result must preserve the raw two-octet field");
    }

    dnp3host::ReadOptions overflow_options;
    overflow_options.timeout_ms = kLocalTaskTimeoutMs;
    overflow_options.max_measurements = 1;
    const auto overflow = backend->integrity_poll(overflow_options);
    check(overflow.error.has_value(), "bounded measurement overflow must fail the operation");
    if (overflow.error) {
        check(
            overflow.error->code == dnp3host::ErrorCode::QueueOverflow,
            "bounded measurement overflow must use QUEUE_OVERFLOW");
        if (overflow.error->code == dnp3host::ErrorCode::QueueOverflow) {
            check(
                overflow.error->details.at("overflow").get<std::uint64_t>() > 0,
                "overflow error must report the dropped result count");
        }
    }

    const auto disconnected = backend->disconnect();
    check(!disconnected.error.has_value(), "local master must disconnect cleanly");
    backend->shutdown();
}

void run_command_integration()
{
    check(TC_APP_CROB_SBO_LOCAL_001 != nullptr, "stable command test ID must exist");
    check(TC_APP_DIRECT_OPERATE_LOCAL_001 != nullptr, "stable command test ID must exist");
    check(
        TC_APP_ANALOG_OUTPUT_BATCH_LOCAL_001 != nullptr,
        "stable command batch test ID must exist");
    check(TC_APP_COMMAND_STATUS_LOCAL_001 != nullptr, "stable status test ID must exist");
    check(TC_APP_CONTROL_SAFETY_LOCAL_001 != nullptr, "stable safety test ID must exist");
    check(
        TC_APP_CONTROL_CHANNEL_GATE_LOCAL_001 != nullptr,
        "stable channel-gate test ID must exist");
    check(TC_APP_DIRECT_OPERATE_NR_GAP_001 != nullptr, "stable gap test ID must exist");

    LocalOutstation outstation;
    auto backend = dnp3host::make_opendnp3_backend();
    dnp3host::CommandConfig preconnect;
    preconnect.safety_token = "0123456789abcdef0123456789abcdef";
    preconnect.commands.push_back(dnp3host::CommandPoint{});
    const auto not_connected = backend->direct_operate(preconnect);
    check(not_connected.error.has_value(), "command before connect must fail");
    if (not_connected.error) {
        check(
            not_connected.error->code == dnp3host::ErrorCode::NotConnected,
            "preconnect command must use NOT_CONNECTED");
    }

    const auto connected = backend->connect(
        authorized_connection_config(outstation.port()));
    check(!connected.error.has_value(), "authorized command session must connect");
    if (connected.error) {
        std::cerr << connected.error->message << ": " << connected.error->details.dump()
                  << '\n';
        return;
    }
    const auto token = connected.result.at("safety").at("safety_token").get<std::string>();
    check(token.size() == 32, "authorized session must return one short-lived token");
    check(backend->status().state_change_authorized, "status must expose locked/unlocked state");

    auto wrong_token = preconnect;
    wrong_token.safety_token = "ffffffffffffffffffffffffffffffff";
    const auto locked = backend->direct_operate(wrong_token);
    check(locked.error.has_value(), "wrong safety token must fail closed");
    if (locked.error) {
        check(
            locked.error->code == dnp3host::ErrorCode::SafetyInterlock,
            "wrong token must use SAFETY_INTERLOCK");
    }

    dnp3host::CommandConfig direct;
    direct.safety_token = token;
    dnp3host::CommandPoint crob;
    crob.kind = dnp3host::CommandKind::Crob;
    crob.index = 0;
    crob.crob_operation = dnp3host::CrobOperation::LatchOn;
    direct.commands.push_back(crob);
    const auto direct_result = backend->direct_operate(direct);
    check(!direct_result.error.has_value(), "CROB Direct Operate must complete");
    if (!direct_result.error) {
        check(direct_result.result.at("all_success") == true, "direct CROB must succeed");
        check(
            direct_result.result.at("point_results").front().at("status") == "SUCCESS",
            "direct CROB must preserve Command Status");
    }

    dnp3host::CommandConfig batch;
    batch.safety_token = token;
    batch.commands.push_back(crob);
    dnp3host::CommandPoint int16;
    int16.kind = dnp3host::CommandKind::AnalogOutputInt16;
    int16.index = 0;
    int16.integer_value = -123;
    batch.commands.push_back(int16);
    dnp3host::CommandPoint int32;
    int32.kind = dnp3host::CommandKind::AnalogOutputInt32;
    int32.index = 0;
    int32.integer_value = 123456;
    batch.commands.push_back(int32);
    dnp3host::CommandPoint float32;
    float32.kind = dnp3host::CommandKind::AnalogOutputFloat32;
    float32.index = 0;
    float32.floating_value = 12.5;
    batch.commands.push_back(float32);
    dnp3host::CommandPoint double64;
    double64.kind = dnp3host::CommandKind::AnalogOutputDouble64;
    double64.index = 0;
    double64.floating_value = -9876.125;
    batch.commands.push_back(double64);

    const auto sbo = backend->select_and_operate(batch);
    check(!sbo.error.has_value(), "mixed SBO command batch must complete");
    if (!sbo.error) {
        check(sbo.result.at("all_success") == true, "every SBO point must succeed");
        check(
            sbo.result.at("summary").at("requested_points") == 5,
            "batch must retain every requested point");
        check(
            sbo.result.at("point_results").size() == 5,
            "batch must return one result per point");
        for (const auto& point : sbo.result.at("point_results")) {
            check(!point.at("requested").is_null(), "point result must correlate its request");
        }
    }

    auto no_response = direct;
    no_response.no_response = true;
    const auto no_response_result = backend->direct_operate(no_response);
    check(no_response_result.error.has_value(), "No Response must not be emulated");
    if (no_response_result.error) {
        check(
            no_response_result.error->code == dnp3host::ErrorCode::UnsupportedByBackend,
            "No Response gap must use UNSUPPORTED_BY_BACKEND");
    }

    outstation.stop();
    for (int attempt = 0; attempt < 100 && backend->status().channel_state == "OPEN";
         ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds{20});
    }
    const auto closed_channel_command = backend->direct_operate(direct);
    check(
        closed_channel_command.error.has_value(),
        "state-changing command on a closed channel must fail");
    if (closed_channel_command.error) {
        check(
            closed_channel_command.error->code == dnp3host::ErrorCode::InvalidState,
            "closed-channel command must use INVALID_STATE");
    }

    backend->disconnect();
    check(
        !backend->status().state_change_authorized,
        "disconnect must expire the safety authorization");
    backend->shutdown();

    LocalOutstation rejecting_outstation(
        std::make_shared<opendnp3::SimpleCommandHandler>(
            opendnp3::CommandStatus::NOT_SUPPORTED));
    auto rejecting_backend = dnp3host::make_opendnp3_backend();
    const auto rejecting_connected = rejecting_backend->connect(
        authorized_connection_config(rejecting_outstation.port()));
    check(!rejecting_connected.error.has_value(), "rejecting outstation must connect");
    if (!rejecting_connected.error) {
        direct.safety_token = rejecting_connected.result.at("safety")
                                  .at("safety_token")
                                  .get<std::string>();
        const auto rejected = rejecting_backend->direct_operate(direct);
        check(!rejected.error.has_value(), "DUT command rejection is a valid task result");
        if (!rejected.error) {
            check(rejected.result.at("all_success") == false, "rejection must fail the point");
            check(
                rejected.result.at("point_results").front().at("status")
                    == "NOT_SUPPORTED",
                "full NOT_SUPPORTED status must be preserved");
        }
        rejecting_backend->disconnect();
    }
    rejecting_backend->shutdown();
}

void run_unsolicited_overflow_integration()
{
    check(
        TC_APP_UNSOLICITED_OVERFLOW_LOCAL_001 != nullptr,
        "stable unsolicited-overflow test ID must exist");
    LocalOutstation outstation;
    auto backend = dnp3host::make_opendnp3_backend(1);
    const auto connected = backend->connect(connection_config(outstation.port()));
    check(!connected.error.has_value(), "overflow-test master must connect");
    if (connected.error) {
        return;
    }

    dnp3host::ReadOptions integrity_options;
    integrity_options.timeout_ms = kLocalTaskTimeoutMs;
    const auto integrity = backend->integrity_poll(integrity_options);
    check_successful_task(integrity);

    dnp3host::UnsolicitedControlConfig control;
    control.timeout_ms = kLocalTaskTimeoutMs;
    control.class_mask = 0x06;
    const auto enabled = backend->enable_unsolicited(control);
    check_successful_task(enabled);
    if (enabled.error) {
        backend->disconnect();
        return;
    }

    outstation.generate_profile_events();
    std::this_thread::sleep_for(std::chrono::milliseconds{250});
    dnp3host::WaitUnsolicitedConfig wait;
    wait.timeout_ms = 1000;
    wait.max_events = 1;
    const auto batch = backend->wait_unsolicited(wait);
    check(!batch.error.has_value(), "overflow-test event wait must succeed");
    if (!batch.error) {
        const auto& summary = batch.result.at("summary");
        check(summary.at("queue_capacity") == 1, "test queue capacity must be one");
        check(
            summary.at("received_total") >= 2,
            "two unsolicited events must reach the bounded store");
        check(
            summary.at("dropped_total") >= 1,
            "drop-oldest overflow must increment the loss counter");
        check(
            batch.result.at("measurements").size() == 1,
            "bounded store must retain no more than its capacity");
    }
    backend->disconnect();
    backend->shutdown();
}

}  // namespace

int main()
{
    try {
        run_read_integration();
        run_unsolicited_overflow_integration();
        run_command_integration();
    }
    catch (const std::exception& error) {
        std::cerr << "UNEXPECTED EXCEPTION: " << error.what() << '\n';
        return 1;
    }
    if (failures != 0) {
        std::cerr << failures << " read integration assertion(s) failed\n";
        return 1;
    }
    return 0;
}
