#include "dnp3host/ProjectInfo.h"

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
    passed = expect_equal(dnp3host::kVersion, "0.5.0", "host version") && passed;
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
    return passed ? 0 : 1;
}
