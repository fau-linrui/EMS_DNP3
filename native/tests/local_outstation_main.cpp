#include <opendnp3/DNP3Manager.h>
#include <opendnp3/app/MeasurementTypes.h>
#include <opendnp3/app/OctetString.h>
#include <opendnp3/channel/IChannel.h>
#include <opendnp3/channel/IPEndpoint.h>
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

#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace {

std::uint16_t parse_port(const int argc, char* argv[])
{
    if (argc != 3 || std::string{argv[1]} != "--port") {
        throw std::invalid_argument("usage: dnp3-local-test-outstation --port <1-65535>");
    }
    const auto parsed = std::stoul(argv[2]);
    if (parsed == 0 || parsed > 65535) {
        throw std::out_of_range("port must be between 1 and 65535");
    }
    return static_cast<std::uint16_t>(parsed);
}

}  // namespace

int main(const int argc, char* argv[])
{
    try {
        const auto port = parse_port(argc, argv);
        opendnp3::DNP3Manager manager(1);
        auto channel = manager.AddTCPServer(
            "pytest-local-outstation",
            opendnp3::levels::NOTHING,
            opendnp3::ServerAcceptMode::CloseExisting,
            opendnp3::IPEndpoint{"127.0.0.1", port},
            nullptr);

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

        auto outstation = channel->AddOutstation(
            "pytest-local-outstation-stack",
            opendnp3::SuccessCommandHandler::Create(),
            opendnp3::DefaultOutstationApplication::Create(),
            config);
        if (!outstation || !outstation->Enable()) {
            throw std::runtime_error("unable to enable local outstation");
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
        updates.Update(opendnp3::Counter{42, opendnp3::Flags{0x01}}, 0);
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
        outstation->Apply(updates.Build());

        std::cout << "{\"ready\":true,\"port\":" << port << "}\n" << std::flush;
        std::string command;
        while (std::getline(std::cin, command)) {
            if (command == "shutdown") {
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
