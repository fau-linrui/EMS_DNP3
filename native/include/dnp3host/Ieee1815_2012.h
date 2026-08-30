#pragma once

#include <array>
#include <cstdint>
#include <string_view>

namespace dnp3host {

inline constexpr std::string_view kTargetProtocolEdition{"IEEE1815-2012"};

inline constexpr std::array<std::string_view, 8> kIin1Names2012{
    "BROADCAST",
    "CLASS1_EVENTS",
    "CLASS2_EVENTS",
    "CLASS3_EVENTS",
    "NEED_TIME",
    "LOCAL_CONTROL",
    "DEVICE_TROUBLE",
    "DEVICE_RESTART"};

inline constexpr std::array<std::string_view, 8> kIin2Names2012{
    "NO_FUNC_CODE_SUPPORT",
    "OBJECT_UNKNOWN",
    "PARAMETER_ERROR",
    "EVENT_BUFFER_OVERFLOW",
    "ALREADY_EXECUTING",
    "CONFIG_CORRUPT",
    "RESERVED_2",
    "RESERVED_1"};

constexpr std::string_view command_status_name_2012(
    const std::uint8_t raw) noexcept
{
    switch (raw) {
    case 0:
        return "SUCCESS";
    case 1:
        return "TIMEOUT";
    case 2:
        return "NO_SELECT";
    case 3:
        return "FORMAT_ERROR";
    case 4:
        return "NOT_SUPPORTED";
    case 5:
        return "ALREADY_ACTIVE";
    case 6:
        return "HARDWARE_ERROR";
    case 7:
        return "LOCAL";
    case 8:
        return "TOO_MANY_OBJS";
    case 9:
        return "NOT_AUTHORIZED";
    case 10:
        return "AUTOMATION_INHIBIT";
    case 11:
        return "PROCESSING_LIMITED";
    case 12:
        return "OUT_OF_RANGE";
    case 126:
        return "NON_PARTICIPATING";
    case 127:
        return "UNDEFINED";
    default:
        return "RESERVED";
    }
}

constexpr bool command_status_is_reserved_2012(
    const std::uint8_t raw) noexcept
{
    return raw >= 13 && raw <= 125;
}

// OpenDNP3 3.1.2 maps every unrecognized wire value to enum value 127.
// Consequently, a decoded 127 cannot distinguish wire UNDEFINED (127) from
// a reserved wire value that the backend does not enumerate.
constexpr bool command_status_wire_raw_unambiguous(
    const std::uint8_t backend_raw) noexcept
{
    return backend_raw != 127;
}

// Project safety policy for a state-changing command result. TIMEOUT does not
// prove non-execution. Reserved values have no 2012-defined disposition, and
// OpenDNP3's decoded 127 may also represent an unrecognized reserved wire value.
// These values therefore require independent readback before another command.
constexpr bool command_status_requires_manual_readback_2012(
    const std::uint8_t backend_raw) noexcept
{
    return backend_raw == 1 || command_status_is_reserved_2012(backend_raw)
        || !command_status_wire_raw_unambiguous(backend_raw);
}

}  // namespace dnp3host
