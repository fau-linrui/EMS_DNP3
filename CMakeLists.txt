cmake_minimum_required(VERSION 3.25)

project(
    dnp3_master_test_framework
    VERSION 0.3.0
    DESCRIPTION "Windows DNP3 master automation test framework"
    LANGUAGES CXX
)

if(NOT WIN32)
    message(WARNING "The supported production target is Windows x64.")
endif()

option(DNP3_ENABLE_ASAN "Enable AddressSanitizer for supported MSVC builds" OFF)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(DNP3_PROTOCOL_SCHEMA_VERSION 1)
set(DNP3_OPENDNP3_LOCK_PATH "${PROJECT_SOURCE_DIR}/third_party/opendnp3.lock.json")
set(
    DNP3_DEPENDENCY_LOCK_PATH
    "${PROJECT_SOURCE_DIR}/third_party/opendnp3-dependencies.lock.json"
)

foreach(dnp3_required_file IN ITEMS
        "${DNP3_OPENDNP3_LOCK_PATH}"
        "${DNP3_DEPENDENCY_LOCK_PATH}"
        "${PROJECT_SOURCE_DIR}/config/capability_matrix.csv")
    if(NOT EXISTS "${dnp3_required_file}")
        message(FATAL_ERROR "Required fixed build input is missing: ${dnp3_required_file}")
    endif()
endforeach()

set_property(
    DIRECTORY
    APPEND
    PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${DNP3_OPENDNP3_LOCK_PATH}"
        "${DNP3_DEPENDENCY_LOCK_PATH}"
        "${PROJECT_SOURCE_DIR}/config/capability_matrix.csv"
)

file(READ "${DNP3_OPENDNP3_LOCK_PATH}" DNP3_OPENDNP3_LOCK_JSON)
string(JSON DNP3_OPENDNP3_VERSION GET "${DNP3_OPENDNP3_LOCK_JSON}" version)
string(JSON DNP3_OPENDNP3_COMMIT GET "${DNP3_OPENDNP3_LOCK_JSON}" commit)
if(NOT DNP3_OPENDNP3_VERSION STREQUAL "3.1.2")
    message(FATAL_ERROR "OpenDNP3 lock must identify version 3.1.2")
endif()
if(NOT DNP3_OPENDNP3_COMMIT STREQUAL "26b4c01e4839bbbda8866655e086471c4917ee53")
    message(FATAL_ERROR "OpenDNP3 lock identifies an unexpected commit")
endif()

file(READ "${DNP3_DEPENDENCY_LOCK_PATH}" DNP3_DEPENDENCY_LOCK_JSON)
string(JSON DNP3_DEPENDENCY_BUILD_POLICY GET "${DNP3_DEPENDENCY_LOCK_JSON}" build_policy)
if(NOT DNP3_DEPENDENCY_BUILD_POLICY STREQUAL "vendored-offline-no-network-fetch")
    message(FATAL_ERROR "OpenDNP3 dependency lock does not enforce the offline build policy")
endif()

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)
execute_process(
    COMMAND
        "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/scripts/validate_dependencies.py"
        "${DNP3_DEPENDENCY_LOCK_PATH}"
        --project-root "${PROJECT_SOURCE_DIR}"
    RESULT_VARIABLE dnp3_dependency_validation_result
    OUTPUT_VARIABLE dnp3_dependency_validation_output
    ERROR_VARIABLE dnp3_dependency_validation_error
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_STRIP_TRAILING_WHITESPACE
)
if(NOT dnp3_dependency_validation_result EQUAL 0)
    message(
        FATAL_ERROR
        "Fixed dependency validation failed:\n"
        "${dnp3_dependency_validation_output}\n"
        "${dnp3_dependency_validation_error}"
    )
endif()
message(STATUS "${dnp3_dependency_validation_output}")

set(DNP3_GIT_COMMIT "unversioned")
set(DNP3_GIT_WORKTREE_STATE "unavailable")
if(EXISTS "${PROJECT_SOURCE_DIR}/.git")
    find_package(Git QUIET)
    if(Git_FOUND)
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
            WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}"
            RESULT_VARIABLE dnp3_git_result
            OUTPUT_VARIABLE dnp3_git_output
            ERROR_QUIET
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )
        string(LENGTH "${dnp3_git_output}" dnp3_git_output_length)
        if(dnp3_git_result EQUAL 0
           AND dnp3_git_output_length EQUAL 40
           AND dnp3_git_output MATCHES "^[0-9a-fA-F]+$")
            string(TOLOWER "${dnp3_git_output}" DNP3_GIT_COMMIT)
        endif()
        execute_process(
            COMMAND "${GIT_EXECUTABLE}" status --porcelain --untracked-files=normal
            WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}"
            RESULT_VARIABLE dnp3_git_status_result
            OUTPUT_VARIABLE dnp3_git_status_output
            ERROR_QUIET
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )
        if(dnp3_git_status_result EQUAL 0)
            if(dnp3_git_status_output STREQUAL "")
                set(DNP3_GIT_WORKTREE_STATE "clean")
            else()
                set(DNP3_GIT_WORKTREE_STATE "dirty")
            endif()
        endif()
    endif()
endif()

if(WIN32)
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
        message(FATAL_ERROR "This project supports only 64-bit Windows builds")
    endif()
    if(CMAKE_GENERATOR_PLATFORM AND NOT CMAKE_GENERATOR_PLATFORM STREQUAL "x64")
        message(FATAL_ERROR "This project requires the x64 Visual Studio platform")
    endif()
    set(DNP3_TARGET_ARCHITECTURE "x64")
else()
    set(DNP3_TARGET_ARCHITECTURE "${CMAKE_SYSTEM_PROCESSOR}")
endif()

file(
    SHA256
    "${PROJECT_SOURCE_DIR}/config/capability_matrix.csv"
    DNP3_CAPABILITY_MATRIX_SHA256
)
file(SHA256 "${DNP3_DEPENDENCY_LOCK_PATH}" DNP3_DEPENDENCY_LOCK_SHA256)
string(TOLOWER "${DNP3_CAPABILITY_MATRIX_SHA256}" DNP3_CAPABILITY_MATRIX_SHA256)
string(TOLOWER "${DNP3_DEPENDENCY_LOCK_SHA256}" DNP3_DEPENDENCY_LOCK_SHA256)

# Keep executable paths stable for scripts and future Python process discovery,
# including when a multi-configuration Visual Studio generator is used.
foreach(dnp3_config IN ITEMS Debug Release RelWithDebInfo MinSizeRel)
    string(TOUPPER "${dnp3_config}" dnp3_config_upper)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_${dnp3_config_upper} "${CMAKE_BINARY_DIR}/bin")
endforeach()

add_library(dnp3host_build_options INTERFACE)
target_compile_features(dnp3host_build_options INTERFACE cxx_std_17)
target_include_directories(
    dnp3host_build_options
    INTERFACE
        "${PROJECT_SOURCE_DIR}/native/include"
)
target_compile_definitions(
    dnp3host_build_options
    INTERFACE
        DNP3HOST_VERSION="${PROJECT_VERSION}"
        DNP3HOST_GIT_COMMIT="${DNP3_GIT_COMMIT}"
        DNP3HOST_SCHEMA_VERSION=${DNP3_PROTOCOL_SCHEMA_VERSION}
        DNP3HOST_CAPABILITY_MATRIX_SHA256="${DNP3_CAPABILITY_MATRIX_SHA256}"
)

if(MSVC)
    target_compile_options(
        dnp3host_build_options
        INTERFACE
            /W4
            /EHsc
            /permissive-
            /utf-8
            /Zc:__cplusplus
    )
else()
    target_compile_options(dnp3host_build_options INTERFACE -Wall -Wextra -Wpedantic)
endif()

if(DNP3_ENABLE_ASAN)
    if(MSVC)
        target_compile_options(dnp3host_build_options INTERFACE /fsanitize=address)
        # MSVC records ASan metadata in each compiled object and link.exe
        # infers the runtime libraries. Passing /fsanitize to link.exe itself
        # is invalid; disabling incremental linking is required for ASan.
        target_link_options(dnp3host_build_options INTERFACE /INCREMENTAL:NO)
    else()
        message(FATAL_ERROR "DNP3_ENABLE_ASAN is currently defined only for MSVC builds")
    endif()
endif()

add_library(nlohmann_json INTERFACE)
add_library(nlohmann_json::nlohmann_json ALIAS nlohmann_json)
target_include_directories(
    nlohmann_json
    INTERFACE
        "${PROJECT_SOURCE_DIR}/third_party/nlohmann_json/single_include"
)

# OpenDNP3 3.1.2 uses FetchContent internally. Redirect every required source
# to the reviewed local tree and make network fallback impossible.
foreach(dnp3_local_dependency IN ITEMS asio exe4cpp ser4cpp)
    if(NOT EXISTS "${PROJECT_SOURCE_DIR}/third_party/${dnp3_local_dependency}")
        message(FATAL_ERROR "Vendored dependency is missing: ${dnp3_local_dependency}")
    endif()
endforeach()
set(FETCHCONTENT_FULLY_DISCONNECTED ON CACHE BOOL "Disable all dependency downloads" FORCE)
set(FETCHCONTENT_UPDATES_DISCONNECTED ON CACHE BOOL "Disable dependency updates" FORCE)
set(CMAKE_POLICY_DEFAULT_CMP0135 NEW)
set(CMAKE_POLICY_DEFAULT_CMP0169 OLD)
set(
    FETCHCONTENT_SOURCE_DIR_ASIO
    "${PROJECT_SOURCE_DIR}/third_party/asio"
    CACHE PATH "Fixed local Asio source" FORCE
)
set(
    FETCHCONTENT_SOURCE_DIR_EXE4CPP
    "${PROJECT_SOURCE_DIR}/third_party/exe4cpp"
    CACHE PATH "Fixed local exe4cpp source" FORCE
)
set(
    FETCHCONTENT_SOURCE_DIR_SER4CPP
    "${PROJECT_SOURCE_DIR}/third_party/ser4cpp"
    CACHE PATH "Fixed local ser4cpp source" FORCE
)
set(DNP3_TLS OFF CACHE BOOL "OpenDNP3 TLS is outside the current task" FORCE)
set(DNP3_TESTS OFF CACHE BOOL "Use project-owned smoke tests" FORCE)
set(DNP3_EXAMPLES OFF CACHE BOOL "Do not build OpenDNP3 examples" FORCE)
set(DNP3_FUZZING OFF CACHE BOOL "Do not build OpenDNP3 fuzz targets" FORCE)
set(DNP3_COVERAGE OFF CACHE BOOL "Do not build OpenDNP3 coverage targets" FORCE)
set(DNP3_JAVA OFF CACHE BOOL "Do not build OpenDNP3 Java bindings" FORCE)
set(DNP3_DOTNET OFF CACHE BOOL "Do not build OpenDNP3 .NET bindings" FORCE)
set(DNP3_EVERYTHING OFF CACHE BOOL "Build only the required OpenDNP3 library" FORCE)
set(DNP3_STATIC_LIBS ON CACHE BOOL "Use a fixed static OpenDNP3 library on Windows" FORCE)
add_subdirectory(
    "${PROJECT_SOURCE_DIR}/third_party/opendnp3"
    "${CMAKE_BINARY_DIR}/third_party/opendnp3"
    EXCLUDE_FROM_ALL
)

if(MSVC)
    # OpenDNP3 3.1.2 checks WIN32 (rather than the compiler-provided _WIN32)
    # in its UTC logging helper and does not add MSVC exception semantics.
    target_compile_definitions(opendnp3 PRIVATE WIN32)
    target_compile_options(opendnp3 PRIVATE /EHsc)
endif()
set_property(
    TARGET opendnp3
    APPEND
    PROPERTY INTERFACE_SYSTEM_INCLUDE_DIRECTORIES
        "$<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/third_party/opendnp3/cpp/lib/include>"
)

if(DNP3_ENABLE_ASAN AND MSVC)
    target_compile_options(opendnp3 PRIVATE /fsanitize=address)
endif()

add_library(
    dnp3host_core
    STATIC
        native/src/CommandConfig.cpp
        native/src/ConnectionConfig.cpp
        native/src/HostController.cpp
        native/src/JsonLineProtocol.cpp
        native/src/OpenDnp3Backend.cpp
        native/src/OpenDnp3CommandSupport.cpp
        native/src/OpenDnp3ReadSupport.cpp
        native/src/OpenDnp3UnsolicitedSupport.cpp
        native/src/ReadConfig.cpp
)
target_link_libraries(
    dnp3host_core
    PUBLIC
        dnp3host_build_options
        nlohmann_json::nlohmann_json
    PRIVATE
        opendnp3
)

add_executable(dnp3-master-host native/src/main.cpp)
target_link_libraries(dnp3-master-host PRIVATE dnp3host_core)

file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/generated")
configure_file(
    "${PROJECT_SOURCE_DIR}/cmake/GenerateBuildInfo.cmake.in"
    "${CMAKE_BINARY_DIR}/generated/GenerateBuildInfo.cmake"
    @ONLY
)
add_custom_target(
    dnp3-build-info ALL
    COMMAND
        "${CMAKE_COMMAND}"
        "-DOUTPUT_PATH=$<TARGET_FILE_DIR:dnp3-master-host>/build-info.json"
        "-DBUILD_CONFIGURATION=$<CONFIG>"
        -P "${CMAKE_BINARY_DIR}/generated/GenerateBuildInfo.cmake"
    DEPENDS dnp3-master-host
    COMMENT "Writing auditable build-info.json"
    VERBATIM
)

include(CTest)
if(BUILD_TESTING)
    add_executable(dnp3host-unit-tests native/tests/project_info_tests.cpp)
    target_link_libraries(dnp3host-unit-tests PRIVATE dnp3host_build_options)

    add_executable(dnp3host-protocol-tests native/tests/protocol_tests.cpp)
    target_link_libraries(dnp3host-protocol-tests PRIVATE dnp3host_core)

    add_executable(
        opendnp3-runtime-tests
        native/tests/opendnp3_runtime_tests.cpp
    )
    target_link_libraries(
        opendnp3-runtime-tests
        PRIVATE
            dnp3host_build_options
            opendnp3
    )

    add_executable(
        opendnp3-read-integration-tests
        native/tests/opendnp3_read_integration_tests.cpp
    )

    add_executable(
        dnp3-local-test-outstation
        native/tests/local_outstation_main.cpp
    )
    target_link_libraries(
        dnp3-local-test-outstation
        PRIVATE
            dnp3host_build_options
            opendnp3
    )
    target_link_libraries(
        opendnp3-read-integration-tests
        PRIVATE
            dnp3host_core
            opendnp3
    )

    add_test(NAME native.project_info COMMAND dnp3host-unit-tests)
    add_test(NAME native.protocol COMMAND dnp3host-protocol-tests)
    add_test(
        NAME native.host_smoke
        COMMAND
            "${CMAKE_COMMAND}"
            "-DHOST_EXECUTABLE=$<TARGET_FILE:dnp3-master-host>"
            "-DHOST_INPUT_FILE=${PROJECT_SOURCE_DIR}/native/tests/host_smoke.ndjson"
            -P "${PROJECT_SOURCE_DIR}/cmake/RunHostSmokeTest.cmake"
    )
    # The inner driver has its own timeout; this outer bound also covers a
    # CMake-driver failure before or after the host process is launched.
    set_tests_properties(native.host_smoke PROPERTIES TIMEOUT 15)
    add_test(NAME native.opendnp3_runtime COMMAND opendnp3-runtime-tests)
    add_test(NAME native.opendnp3_read_integration COMMAND opendnp3-read-integration-tests)

    install(
        TARGETS dnp3-local-test-outstation
        RUNTIME DESTINATION tools
    )
endif()

install(TARGETS dnp3-master-host RUNTIME DESTINATION bin)
install(
    FILES "$<TARGET_FILE_DIR:dnp3-master-host>/build-info.json"
    DESTINATION bin
)
install(DIRECTORY "${PROJECT_SOURCE_DIR}/schemas/" DESTINATION schemas)
install(
    FILES
        "${PROJECT_SOURCE_DIR}/config/capability_matrix.csv"
        "${PROJECT_SOURCE_DIR}/config/ems_profile.example.json"
        "${PROJECT_SOURCE_DIR}/config/points.example.csv"
    DESTINATION config
)
install(FILES NOTICE.txt THIRD_PARTY_LICENSES.txt DESTINATION .)
install(DIRECTORY "${PROJECT_SOURCE_DIR}/LICENSES/" DESTINATION licenses)
install(
    FILES
        "${PROJECT_SOURCE_DIR}/third_party/opendnp3.lock.json"
        "${PROJECT_SOURCE_DIR}/third_party/opendnp3-dependencies.lock.json"
        "${PROJECT_SOURCE_DIR}/third_party/nlohmann_json.lock.json"
    DESTINATION dependency-locks
)
