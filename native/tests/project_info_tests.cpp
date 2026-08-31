#include "dnp3host/ProjectInfo.h"
#include "dnp3host/Ieee1815_2012.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <string>
#include <string_view>

namespace {

bool expect_equal(
    const std::string_view actual,
    const std::string_view expected,
    const std::string_view field)
{
    if (actual == expected) {
        return true;
    }

    std::cerr << field << " mismatch: expected '" << expected << "', got '" << actual << "'\n";
    return false;
}

bool expect_true(const bool condition, const std::string_view message)
{
    if (condition) {
        return true;
    }
    std::cerr << message << '\n';
    return false;
}

}  // namespace

int main()
{
    bool passed = true;
    passed = expect_equal(
                 dnp3host::kProjectName,
                 "dnp3-master-test-framework",
                 "project name")
        && passed;
    passed = expect_equal(
                 dnp3host::kHostExecutableName,
                 "dnp3-master-host",
                 "host executable name")
        && passed;
    passed = expect_equal(dnp3host::kVersion, "0.6.1", "host version") && passed;
    passed = expect_equal(
                 dnp3host::kTargetStandard,
                 "IEEE1815-2012",
                 "target standard")
        && passed;
    passed = expect_true(dnp3host::kSchemaVersion == 1, "schema version must be 1")
        && passed;
    passed = expect_equal(dnp3host::kPlatform, "windows-x64", "platform") && passed;
    passed = expect_equal(
                 dnp3host::kCapabilityMatrixVersion,
                 "1",
                 "capability matrix version")
        && passed;
    passed = expect_true(
                 dnp3host::kCapabilityMatrixSha256.size() == 64,
                 "capability matrix SHA-256 must contain 64 hexadecimal characters")
        && passed;
    constexpr std::array<std::string_view, 8> expected_iin2{
        "NO_FUNC_CODE_SUPPORT",
        "OBJECT_UNKNOWN",
        "PARAMETER_ERROR",
        "EVENT_BUFFER_OVERFLOW",
        "ALREADY_EXECUTING",
        "CONFIG_CORRUPT",
        "RESERVED_2",
        "RESERVED_1"};
    for (std::size_t index = 0; index < expected_iin2.size(); ++index) {
        passed = expect_equal(
                     dnp3host::kIin2Names2012[index],
                     expected_iin2[index],
                     "IEEE 1815-2012 IIN2 name")
            && passed;
    }
    constexpr std::array<std::string_view, 13> expected_command_status{
        "SUCCESS",
        "TIMEOUT",
        "NO_SELECT",
        "FORMAT_ERROR",
        "NOT_SUPPORTED",
        "ALREADY_ACTIVE",
        "HARDWARE_ERROR",
        "LOCAL",
        "TOO_MANY_OBJS",
        "NOT_AUTHORIZED",
        "AUTOMATION_INHIBIT",
        "PROCESSING_LIMITED",
        "OUT_OF_RANGE"};
    for (std::size_t raw = 0; raw < expected_command_status.size(); ++raw) {
        passed = expect_equal(
                     dnp3host::command_status_name_2012(
                         static_cast<std::uint8_t>(raw)),
                     expected_command_status[raw],
                     "IEEE 1815-2012 command status name")
            && passed;
    }
    passed = expect_equal(
                 dnp3host::command_status_name_2012(13),
                 "RESERVED",
                 "IEEE 1815-2012 reserved command status")
        && passed;
    passed = expect_equal(
                 dnp3host::command_status_name_2012(126),
                 "NON_PARTICIPATING",
                 "IEEE 1815-2012 command status 126")
        && passed;
    passed = expect_true(
                 dnp3host::command_status_is_reserved_2012(18),
                 "OpenDNP3 later-edition status 18 must remain reserved in 2012 mode")
        && passed;
    passed = expect_true(
                 !dnp3host::command_status_wire_raw_unambiguous(127),
                 "decoded status 127 must expose the OpenDNP3 raw-wire ambiguity")
        && passed;
    passed = expect_true(
                 dnp3host::command_status_requires_manual_readback_2012(1),
                 "TIMEOUT must require independent readback")
        && passed;
    passed = expect_true(
                 dnp3host::command_status_requires_manual_readback_2012(18),
                 "a reserved 2012 status must require independent readback")
        && passed;
    passed = expect_true(
                 dnp3host::command_status_requires_manual_readback_2012(127),
                 "ambiguous decoded status 127 must require independent readback")
        && passed;
    passed = expect_true(
                 !dnp3host::command_status_requires_manual_readback_2012(2),
                 "definitive NO_SELECT rejection must not be labeled uncertain")
        && passed;
    return passed ? 0 : 1;
}
