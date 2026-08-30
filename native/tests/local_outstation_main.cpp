#include "dnp3host/JsonLineProtocol.h"

#include <opendnp3/DNP3Manager.h>
#include <opendnp3/app/AnalogOutput.h>
#include <opendnp3/app/ControlRelayOutputBlock.h>
#include <opendnp3/app/MeasurementTypes.h>
#include <opendnp3/app/OctetString.h>
#include <opendnp3/channel/IChannel.h>
#include <opendnp3/channel/IPEndpoint.h>
#include <opendnp3/gen/CommandStatus.h>
#include <opendnp3/gen/EventAnalogVariation.h>
#include <opendnp3/gen/EventBinaryVariation.h>
#include <opendnp3/gen/EventMode.h>
#include <opendnp3/gen/OperateType.h>
#include <opendnp3/gen/OperationType.h>
#include <opendnp3/gen/PointClass.h>
#include <opendnp3/gen/ServerAcceptMode.h>
#include <opendnp3/gen/StaticAnalogOutputStatusVariation.h>
#include <opendnp3/gen/StaticAnalogVariation.h>
#include <opendnp3/gen/StaticBinaryOutputStatusVariation.h>
#include <opendnp3/gen/StaticBinaryVariation.h>
#include <opendnp3/logging/LogLevels.h>
#include <opendnp3/outstation/DefaultOutstationApplication.h>
#include <opendnp3/outstation/EventBufferConfig.h>
#include <opendnp3/outstation/ICommandHandler.h>
#include <opendnp3/outstation/IOutstation.h>
#include <opendnp3/outstation/OutstationStackConfig.h>
#include <opendnp3/outstation/UpdateBuilder.h>

#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_set>

namespace {

using dnp3host::Json;

constexpr std::size_t kPointCount = 2;
constexpr std::size_t kMaxControlRequestBytes = 64U * 1024U;
constexpr std::uint64_t kMaximumDnp3Timestamp = (1ULL << 48U) - 1ULL;

std::optional<std::uint16_t> parse_port(const std::string_view text)
{
    std::uint32_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()
        || value == 0 || value > 65535) {
        return std::nullopt;
    }
    return static_cast<std::uint16_t>(value);
}

std::optional<std::uint16_t> parse_arguments(const int argc, char* argv[])
{
    if (argc != 3 || std::string_view{argv[1]} != "--port") {
        return std::nullopt;
    }
    return parse_port(argv[2]);
}

class StatefulCommandHandler final : public opendnp3::ICommandHandler {
public:
    void Begin() override {}
    void End() override {}

    opendnp3::CommandStatus Select(
        const opendnp3::ControlRelayOutputBlock& command,
        const std::uint16_t index) override
    {
        bool ignored = false;
        return index < kPointCount && crob_value(command, ignored)
            ? opendnp3::CommandStatus::SUCCESS
            : (index < kPointCount ? opendnp3::CommandStatus::FORMAT_ERROR
                                   : opendnp3::CommandStatus::OUT_OF_RANGE);
    }

    opendnp3::CommandStatus Operate(
        const opendnp3::ControlRelayOutputBlock& command,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type) override
    {
        if (index >= kPointCount) {
            return opendnp3::CommandStatus::OUT_OF_RANGE;
        }
        bool value = false;
        if (!crob_value(command, value)) {
            return opendnp3::CommandStatus::FORMAT_ERROR;
        }
        if (!handler.Update(
                opendnp3::BinaryOutputStatus{value, opendnp3::Flags{0x01}},
                index,
                opendnp3::EventMode::Suppress)) {
            return opendnp3::CommandStatus::OUT_OF_RANGE;
        }
        {
            const std::lock_guard<std::mutex> lock{mutex_};
            binary_output_status_[index] = value;
            ++operation_count_;
            ++crob_operation_count_;
            record_operate_type(operate_type);
        }
        return opendnp3::CommandStatus::SUCCESS;
    }

    opendnp3::CommandStatus Select(
        const opendnp3::AnalogOutputInt16&,
        const std::uint16_t index) override
    {
        return select_analog(index);
    }

    opendnp3::CommandStatus Operate(
        const opendnp3::AnalogOutputInt16& command,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type) override
    {
        return operate_analog(command.value, index, handler, operate_type);
    }

    opendnp3::CommandStatus Select(
        const opendnp3::AnalogOutputInt32&,
        const std::uint16_t index) override
    {
        return select_analog(index);
    }

    opendnp3::CommandStatus Operate(
        const opendnp3::AnalogOutputInt32& command,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type) override
    {
        return operate_analog(command.value, index, handler, operate_type);
    }

    opendnp3::CommandStatus Select(
        const opendnp3::AnalogOutputFloat32& command,
        const std::uint16_t index) override
    {
        return select_analog(index, command.value);
    }

    opendnp3::CommandStatus Operate(
        const opendnp3::AnalogOutputFloat32& command,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type) override
    {
        return operate_analog(command.value, index, handler, operate_type);
    }

    opendnp3::CommandStatus Select(
        const opendnp3::AnalogOutputDouble64& command,
        const std::uint16_t index) override
    {
        return select_analog(index, command.value);
    }

    opendnp3::CommandStatus Operate(
        const opendnp3::AnalogOutputDouble64& command,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type) override
    {
        return operate_analog(command.value, index, handler, operate_type);
    }

    void record_external_binary_output(const std::uint16_t index, const bool value)
    {
        const std::lock_guard<std::mutex> lock{mutex_};
        binary_output_status_[index] = value;
    }

    void record_external_analog_output(const std::uint16_t index, const double value)
    {
        const std::lock_guard<std::mutex> lock{mutex_};
        analog_output_status_[index] = value;
    }

    Json snapshot() const
    {
        const std::lock_guard<std::mutex> lock{mutex_};
        return Json{
            {"operation_count", operation_count_},
            {"crob_operation_count", crob_operation_count_},
            {"analog_operation_count", analog_operation_count_},
            {"select_before_operate_count", select_before_operate_count_},
            {"direct_operate_count", direct_operate_count_},
            {"direct_operate_no_ack_count", direct_operate_no_ack_count_},
            {"binary_output_status",
             Json::array({binary_output_status_[0], binary_output_status_[1]})},
            {"analog_output_status",
             Json::array({analog_output_status_[0], analog_output_status_[1]})}};
    }

private:
    static bool crob_value(
        const opendnp3::ControlRelayOutputBlock& command,
        bool& value) noexcept
    {
        switch (command.opType) {
        case opendnp3::OperationType::PULSE_ON:
        case opendnp3::OperationType::LATCH_ON:
            value = true;
            return true;
        case opendnp3::OperationType::PULSE_OFF:
        case opendnp3::OperationType::LATCH_OFF:
            value = false;
            return true;
        case opendnp3::OperationType::NUL:
        case opendnp3::OperationType::Undefined:
            return false;
        }
        return false;
    }

    static opendnp3::CommandStatus select_analog(const std::uint16_t index) noexcept
    {
        return index < kPointCount ? opendnp3::CommandStatus::SUCCESS
                                   : opendnp3::CommandStatus::OUT_OF_RANGE;
    }

    static opendnp3::CommandStatus select_analog(
        const std::uint16_t index,
        const double value) noexcept
    {
        if (!std::isfinite(value)) {
            return opendnp3::CommandStatus::OUT_OF_RANGE;
        }
        return select_analog(index);
    }

    template<class T>
    opendnp3::CommandStatus operate_analog(
        const T value,
        const std::uint16_t index,
        opendnp3::IUpdateHandler& handler,
        const opendnp3::OperateType operate_type)
    {
        const auto normalized = static_cast<double>(value);
        if (index >= kPointCount || !std::isfinite(normalized)) {
            return opendnp3::CommandStatus::OUT_OF_RANGE;
        }
        if (!handler.Update(
                opendnp3::AnalogOutputStatus{normalized, opendnp3::Flags{0x01}},
                index,
                opendnp3::EventMode::Suppress)) {
            return opendnp3::CommandStatus::OUT_OF_RANGE;
        }
        {
            const std::lock_guard<std::mutex> lock{mutex_};
            analog_output_status_[index] = normalized;
            ++operation_count_;
            ++analog_operation_count_;
            record_operate_type(operate_type);
        }
        return opendnp3::CommandStatus::SUCCESS;
    }

    void record_operate_type(const opendnp3::OperateType operate_type) noexcept
    {
        switch (operate_type) {
        case opendnp3::OperateType::SelectBeforeOperate:
            ++select_before_operate_count_;
            break;
        case opendnp3::OperateType::DirectOperate:
            ++direct_operate_count_;
            break;
        case opendnp3::OperateType::DirectOperateNoAck:
            ++direct_operate_no_ack_count_;
            break;
        }
    }

    mutable std::mutex mutex_;
    std::array<bool, kPointCount> binary_output_status_{{false, false}};
    std::array<double, kPointCount> analog_output_status_{{0.0, 0.0}};
    std::uint64_t operation_count_{0};
    std::uint64_t crob_operation_count_{0};
    std::uint64_t analog_operation_count_{0};
    std::uint64_t select_before_operate_count_{0};
    std::uint64_t direct_operate_count_{0};
    std::uint64_t direct_operate_no_ack_count_{0};
};

dnp3host::ProtocolError invalid_request(
    const std::string& id,
    const std::string& message,
    const std::string& reason)
{
    return dnp3host::ProtocolError{
        id,
        dnp3host::ErrorCode::InvalidRequest,
        message,
        Json{{"reason", reason}}};
}

std::optional<dnp3host::ProtocolError> require_fields(
    const dnp3host::Request& request,
    const std::unordered_set<std::string>& required,
    const std::unordered_set<std::string>& optional = {})
{
    for (const auto& field : required) {
        if (!request.params.contains(field)) {
            auto error = invalid_request(
                request.id, "params is missing a required field", "missing_field");
            error.details["field"] = field;
            return error;
        }
    }
    for (const auto& item : request.params.items()) {
        if (required.find(item.key()) == required.end()
            && optional.find(item.key()) == optional.end()) {
            auto error = invalid_request(
                request.id, "params contains an unknown field", "unknown_field");
            error.details["field"] = item.key();
            return error;
        }
    }
    return std::nullopt;
}

std::optional<dnp3host::ProtocolError> require_empty_params(
    const dnp3host::Request& request)
{
    if (!request.params.empty()) {
        return invalid_request(
            request.id, "command requires empty params", "params_not_empty");
    }
    return std::nullopt;
}

std::optional<std::uint16_t> point_index(const Json& value)
{
    if (!value.is_number_unsigned() && !value.is_number_integer()) {
        return std::nullopt;
    }
    if (value.is_number_unsigned()) {
        const auto parsed = value.get<std::uint64_t>();
        return parsed < kPointCount
            ? std::optional<std::uint16_t>{static_cast<std::uint16_t>(parsed)}
            : std::nullopt;
    }
    const auto parsed = value.get<std::int64_t>();
    if (parsed < 0 || parsed >= static_cast<std::int64_t>(kPointCount)) {
        return std::nullopt;
    }
    return static_cast<std::uint16_t>(parsed);
}

std::optional<std::uint64_t> timestamp(const Json& value)
{
    if (!value.is_number_unsigned() && !value.is_number_integer()) {
        return std::nullopt;
    }
    if (value.is_number_unsigned()) {
        const auto parsed = value.get<std::uint64_t>();
        return parsed <= kMaximumDnp3Timestamp
            ? std::optional<std::uint64_t>{parsed}
            : std::nullopt;
    }
    const auto parsed = value.get<std::int64_t>();
    return parsed >= 0 && static_cast<std::uint64_t>(parsed) <= kMaximumDnp3Timestamp
        ? std::optional<std::uint64_t>{static_cast<std::uint64_t>(parsed)}
        : std::nullopt;
}

std::optional<opendnp3::EventMode> event_mode(const Json& value)
{
    if (!value.is_string()) {
        return std::nullopt;
    }
    const auto mode = value.get<std::string>();
    if (mode == "detect") {
        return opendnp3::EventMode::Detect;
    }
    if (mode == "force") {
        return opendnp3::EventMode::Force;
    }
    if (mode == "suppress") {
        return opendnp3::EventMode::Suppress;
    }
    if (mode == "event_only") {
        return opendnp3::EventMode::EventOnly;
    }
    return std::nullopt;
}

std::optional<double> finite_number(const Json& value)
{
    if (!value.is_number()) {
        return std::nullopt;
    }
    const auto parsed = value.get<double>();
    return std::isfinite(parsed) ? std::optional<double>{parsed} : std::nullopt;
}

struct DispatchResult {
    Json response;
    bool should_stop{false};
};

DispatchResult dispatch(
    const dnp3host::Request& request,
    const std::shared_ptr<opendnp3::IOutstation>& outstation,
    const std::shared_ptr<StatefulCommandHandler>& command_handler,
    const std::uint16_t port)
{
    if (request.command == "hello") {
        if (const auto error = require_empty_params(request)) {
            return {dnp3host::JsonLineProtocol::error_response(*error), false};
        }
        return {
            dnp3host::JsonLineProtocol::success_response(
                request.id,
                Json{
                    {"role", "local_test_outstation"},
                    {"bind_host", "127.0.0.1"},
                    {"port", port},
                    {"point_count_per_type", kPointCount},
                    {"supported_commands",
                     Json::array({"hello", "snapshot", "update", "shutdown"})},
                    {"max_request_bytes", kMaxControlRequestBytes}}),
            false};
    }
    if (request.command == "snapshot") {
        if (const auto error = require_empty_params(request)) {
            return {dnp3host::JsonLineProtocol::error_response(*error), false};
        }
        return {
            dnp3host::JsonLineProtocol::success_response(
                request.id, command_handler->snapshot()),
            false};
    }
    if (request.command == "shutdown") {
        if (const auto error = require_empty_params(request)) {
            return {dnp3host::JsonLineProtocol::error_response(*error), false};
        }
        return {
            dnp3host::JsonLineProtocol::success_response(
                request.id, Json{{"state", "SHUTTING_DOWN"}}),
            true};
    }
    if (request.command != "update") {
        auto error = invalid_request(
            request.id,
            "command is not supported by the local outstation",
            "unknown_command");
        error.details["command"] = request.command;
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }

    if (const auto error = require_fields(
            request,
            {"type", "index", "value", "event_mode"},
            {"timestamp_ms"})) {
        return {dnp3host::JsonLineProtocol::error_response(*error), false};
    }
    if (!request.params["type"].is_string()) {
        const auto error = invalid_request(
            request.id, "type must be a string", "invalid_type");
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }
    const auto type = request.params["type"].get<std::string>();
    const auto index = point_index(request.params["index"]);
    if (!index) {
        auto error = invalid_request(
            request.id,
            "index is outside the local test database",
            "invalid_index");
        error.details["maximum"] = kPointCount - 1;
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }
    const auto mode = event_mode(request.params["event_mode"]);
    if (!mode) {
        const auto error = invalid_request(
            request.id, "event_mode is invalid", "invalid_event_mode");
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }

    const bool is_input = type == "binary_input" || type == "analog_input";
    if (is_input != request.params.contains("timestamp_ms")) {
        const auto error = invalid_request(
            request.id,
            is_input ? "input updates require timestamp_ms"
                     : "output status updates must not contain timestamp_ms",
            "invalid_timestamp_presence");
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }
    std::optional<std::uint64_t> timestamp_ms;
    if (is_input) {
        timestamp_ms = timestamp(request.params["timestamp_ms"]);
        if (!timestamp_ms) {
            const auto error = invalid_request(
                request.id,
                "timestamp_ms must be a 48-bit unsigned integer",
                "invalid_timestamp");
            return {dnp3host::JsonLineProtocol::error_response(error), false};
        }
    }

    bool applied = false;
    Json normalized_value;
    std::optional<bool> external_binary_output;
    std::optional<double> external_analog_output;
    opendnp3::UpdateBuilder updates;
    if (type == "binary_input") {
        if (!request.params["value"].is_boolean()) {
            const auto error = invalid_request(
                request.id,
                "binary_input value must be boolean",
                "invalid_value");
            return {dnp3host::JsonLineProtocol::error_response(error), false};
        }
        const auto value = request.params["value"].get<bool>();
        applied = updates.Update(
            opendnp3::Binary{
                value, opendnp3::Flags{0x01}, opendnp3::DNPTime{*timestamp_ms}},
            *index,
            *mode);
        normalized_value = value;
    }
    else if (type == "analog_input") {
        const auto value = finite_number(request.params["value"]);
        if (!value) {
            const auto error = invalid_request(
                request.id,
                "analog_input value must be finite",
                "invalid_value");
            return {dnp3host::JsonLineProtocol::error_response(error), false};
        }
        applied = updates.Update(
            opendnp3::Analog{
                *value, opendnp3::Flags{0x01}, opendnp3::DNPTime{*timestamp_ms}},
            *index,
            *mode);
        normalized_value = *value;
    }
    else if (type == "binary_output_status") {
        if (!request.params["value"].is_boolean()) {
            const auto error = invalid_request(
                request.id,
                "binary_output_status value must be boolean",
                "invalid_value");
            return {dnp3host::JsonLineProtocol::error_response(error), false};
        }
        const auto value = request.params["value"].get<bool>();
        applied = updates.Update(
            opendnp3::BinaryOutputStatus{value, opendnp3::Flags{0x01}},
            *index,
            *mode);
        external_binary_output = value;
        normalized_value = value;
    }
    else if (type == "analog_output_status") {
        const auto value = finite_number(request.params["value"]);
        if (!value) {
            const auto error = invalid_request(
                request.id,
                "analog_output_status value must be finite",
                "invalid_value");
            return {dnp3host::JsonLineProtocol::error_response(error), false};
        }
        applied = updates.Update(
            opendnp3::AnalogOutputStatus{*value, opendnp3::Flags{0x01}},
            *index,
            *mode);
        external_analog_output = *value;
        normalized_value = *value;
    }
    else {
        auto error = invalid_request(
            request.id,
            "type is not supported by the local outstation",
            "unsupported_type");
        error.details["type"] = type;
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }

    if (!applied) {
        const auto error = dnp3host::ProtocolError{
            request.id,
            dnp3host::ErrorCode::InternalError,
            "local outstation rejected a validated update",
            Json::object()};
        return {dnp3host::JsonLineProtocol::error_response(error), false};
    }
    if (external_binary_output) {
        command_handler->record_external_binary_output(
            *index, *external_binary_output);
    }
    if (external_analog_output) {
        command_handler->record_external_analog_output(
            *index, *external_analog_output);
    }
    outstation->Apply(updates.Build());
    Json result{
        {"type", type},
        {"index", *index},
        {"value", std::move(normalized_value)},
        {"event_mode", request.params["event_mode"]}};
    if (timestamp_ms) {
        result["timestamp_ms"] = *timestamp_ms;
    }
    return {
        dnp3host::JsonLineProtocol::success_response(request.id, std::move(result)),
        false};
}

bool write_response(const Json& response)
{
    std::cout << dnp3host::JsonLineProtocol::serialize(response) << '\n';
    std::cout.flush();
    return static_cast<bool>(std::cout);
}

}  // namespace

int main(const int argc, char* argv[])
{
    const auto port = parse_arguments(argc, argv);
    if (!port) {
        std::cerr << "usage: dnp3-local-test-outstation --port <1-65535>\n";
        return 2;
    }

    try {
        opendnp3::DNP3Manager manager(1);
        auto channel = manager.AddTCPServer(
            "pytest-local-outstation",
            opendnp3::levels::NOTHING,
            opendnp3::ServerAcceptMode::CloseExisting,
            opendnp3::IPEndpoint{"127.0.0.1", *port},
            nullptr);

        opendnp3::DatabaseConfig database(kPointCount);
        for (std::uint16_t index = 0;
             index < static_cast<std::uint16_t>(kPointCount);
             ++index) {
            database.binary_input[index].clazz = opendnp3::PointClass::Class1;
            database.binary_input[index].svariation =
                opendnp3::StaticBinaryVariation::Group1Var2;
            database.binary_input[index].evariation =
                opendnp3::EventBinaryVariation::Group2Var2;
            database.analog_input[index].clazz = opendnp3::PointClass::Class2;
            database.analog_input[index].svariation =
                opendnp3::StaticAnalogVariation::Group30Var5;
            database.analog_input[index].evariation =
                opendnp3::EventAnalogVariation::Group32Var7;
            database.binary_output_status[index].svariation =
                opendnp3::StaticBinaryOutputStatusVariation::Group10Var2;
            database.analog_output_status[index].svariation =
                opendnp3::StaticAnalogOutputStatusVariation::Group40Var3;
        }
        opendnp3::OutstationStackConfig config(database);
        config.outstation.eventBufferConfig = opendnp3::EventBufferConfig::AllTypes(32);
        config.outstation.params.allowUnsolicited = true;
        config.link.LocalAddr = 1024;
        config.link.RemoteAddr = 1;

        auto command_handler = std::make_shared<StatefulCommandHandler>();
        auto outstation = channel->AddOutstation(
            "pytest-local-outstation-stack",
            command_handler,
            opendnp3::DefaultOutstationApplication::Create(),
            config);
        if (!outstation || !outstation->Enable()) {
            throw std::runtime_error("unable to enable local outstation");
        }

        const std::uint8_t octets[] = {0xDE, 0xAD, 0xBE, 0xEF};
        opendnp3::UpdateBuilder updates;
        updates.Update(
            opendnp3::Binary{
                false, opendnp3::Flags{0x01}, opendnp3::DNPTime{1700000000001ULL}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::DoubleBitBinary{
                opendnp3::DoubleBit::DETERMINED_ON,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000002ULL}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::Analog{
                123.5,
                opendnp3::Flags{0x01},
                opendnp3::DNPTime{1700000000004ULL}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::Counter{42, opendnp3::Flags{0x01}},
            0,
            opendnp3::EventMode::Suppress);
        updates.FreezeCounter(0, false, opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::BinaryOutputStatus{false, opendnp3::Flags{0x01}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::AnalogOutputStatus{0.0, opendnp3::Flags{0x01}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::OctetString{opendnp3::Buffer{octets, sizeof(octets)}},
            0,
            opendnp3::EventMode::Suppress);
        updates.Update(
            opendnp3::TimeAndInterval{
                opendnp3::DNPTime{1700000000003ULL},
                15,
                opendnp3::IntervalUnits::Seconds},
            0);
        outstation->Apply(updates.Build());

        std::cout << "{\"ready\":true,\"port\":" << *port << "}\n" << std::flush;
        while (true) {
            auto line = dnp3host::JsonLineProtocol::read_line(
                std::cin, kMaxControlRequestBytes);
            if (line.status == dnp3host::ReadLineStatus::EndOfFile) {
                break;
            }
            if (line.status == dnp3host::ReadLineStatus::IoError) {
                throw std::runtime_error("failed to read local control input");
            }
            if (line.status == dnp3host::ReadLineStatus::TooLarge) {
                const auto error = dnp3host::ProtocolError{
                    std::nullopt,
                    dnp3host::ErrorCode::RequestTooLarge,
                    "request line exceeds the local control limit",
                    Json{{"max_request_bytes", kMaxControlRequestBytes}}};
                if (!write_response(dnp3host::JsonLineProtocol::error_response(error))) {
                    throw std::runtime_error("failed to write local control response");
                }
                continue;
            }
            if (line.line == "shutdown") {
                break;
            }

            auto parsed = dnp3host::JsonLineProtocol::parse_request(line.line);
            if (parsed.error) {
                if (!write_response(
                        dnp3host::JsonLineProtocol::error_response(*parsed.error))) {
                    throw std::runtime_error("failed to write local control response");
                }
                continue;
            }

            const auto result = dispatch(*parsed.request, outstation, command_handler, *port);
            if (!write_response(result.response)) {
                throw std::runtime_error("failed to write local control response");
            }
            if (result.should_stop) {
                break;
            }
        }

        outstation->Disable();
        outstation->Shutdown();
        channel->Shutdown();
        manager.Shutdown();
        return 0;
    }
    catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
