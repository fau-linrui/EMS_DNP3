#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

#ifndef DNP3HOST_VERSION
#error "DNP3HOST_VERSION must be supplied by the build system"
#endif

#ifndef DNP3HOST_GIT_COMMIT
#error "DNP3HOST_GIT_COMMIT must be supplied by the build system"
#endif

#ifndef DNP3HOST_SCHEMA_VERSION
#error "DNP3HOST_SCHEMA_VERSION must be supplied by the build system"
#endif

#ifndef DNP3HOST_CAPABILITY_MATRIX_SHA256
#error "DNP3HOST_CAPABILITY_MATRIX_SHA256 must be supplied by the build system"
#endif

namespace dnp3host {

inline constexpr std::string_view kProjectName{"dnp3-master-test-framework"};
inline constexpr std::string_view kHostExecutableName{"dnp3-master-host"};
inline constexpr std::string_view kVersion{DNP3HOST_VERSION};
inline constexpr std::string_view kTargetStandard{"IEEE1815-2012"};
inline constexpr std::uint32_t kSchemaVersion{DNP3HOST_SCHEMA_VERSION};
inline constexpr std::size_t kDefaultMaxRequestBytes{1024U * 1024U};
inline constexpr std::size_t kMaximumConfigurableRequestBytes{16U * 1024U * 1024U};
inline constexpr std::string_view kPlatform{"windows-x64"};
inline constexpr std::string_view kGitCommit{DNP3HOST_GIT_COMMIT};
inline constexpr std::string_view kCapabilityMatrixVersion{"1"};
inline constexpr std::string_view kCapabilityMatrixSha256{
    DNP3HOST_CAPABILITY_MATRIX_SHA256};

}  // namespace dnp3host
