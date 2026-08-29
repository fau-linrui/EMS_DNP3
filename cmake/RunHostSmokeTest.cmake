cmake_minimum_required(VERSION 3.25)

if(NOT DEFINED HOST_EXECUTABLE OR HOST_EXECUTABLE STREQUAL "")
    message(FATAL_ERROR "HOST_EXECUTABLE was not provided")
endif()
if(NOT EXISTS "${HOST_EXECUTABLE}")
    message(FATAL_ERROR "Host executable does not exist: ${HOST_EXECUTABLE}")
endif()
if(NOT DEFINED HOST_INPUT_FILE OR HOST_INPUT_FILE STREQUAL "")
    message(FATAL_ERROR "HOST_INPUT_FILE was not provided")
endif()
if(NOT EXISTS "${HOST_INPUT_FILE}")
    message(FATAL_ERROR "Host smoke input does not exist: ${HOST_INPUT_FILE}")
endif()

# INPUT_FILE gives the long-running host a private, bounded stdin pipe. The
# second request asks it to shut down explicitly, so success never depends on
# whether CTest inherited an interactive terminal or an already-closed pipe.
execute_process(
    COMMAND "${HOST_EXECUTABLE}"
    INPUT_FILE "${HOST_INPUT_FILE}"
    OUTPUT_VARIABLE host_stdout
    ERROR_VARIABLE host_stderr
    RESULT_VARIABLE host_result
    TIMEOUT 10
)

if(NOT "${host_result}" STREQUAL "0")
    message(
        FATAL_ERROR
        "Host smoke process failed or timed out (${host_result}).\n"
        "stdout:\n${host_stdout}\n"
        "stderr:\n${host_stderr}"
    )
endif()

string(REPLACE "\r\n" "\n" normalized_stdout "${host_stdout}")
string(REPLACE "\r" "\n" normalized_stdout "${normalized_stdout}")
string(REGEX REPLACE "\n+$" "" normalized_stdout "${normalized_stdout}")
if(NOT normalized_stdout MATCHES "^([^\n]+)\n([^\n]+)$")
    message(
        FATAL_ERROR
        "Host smoke expected exactly two NDJSON responses.\n"
        "stdout:\n${host_stdout}\n"
        "stderr:\n${host_stderr}"
    )
endif()
set(hello_response "${CMAKE_MATCH_1}")
set(shutdown_response "${CMAKE_MATCH_2}")

function(dnp3_json_get output_variable json_document)
    string(
        JSON parsed_value
        ERROR_VARIABLE parsed_error
        GET "${json_document}" ${ARGN}
    )
    if(NOT parsed_error STREQUAL "NOTFOUND")
        message(
            FATAL_ERROR
            "Invalid host smoke JSON at '${ARGN}': ${parsed_error}\n"
            "document: ${json_document}"
        )
    endif()
    set("${output_variable}" "${parsed_value}" PARENT_SCOPE)
endfunction()

dnp3_json_get(hello_schema "${hello_response}" schema_version)
dnp3_json_get(hello_id "${hello_response}" id)
dnp3_json_get(hello_ok "${hello_response}" ok)
dnp3_json_get(hello_backend "${hello_response}" result backend)
dnp3_json_get(hello_backend_version "${hello_response}" result backend_version)
if(
    NOT hello_schema STREQUAL "1"
    OR NOT hello_id STREQUAL "smoke-hello"
    OR NOT hello_ok
    OR NOT hello_backend STREQUAL "opendnp3"
    OR NOT hello_backend_version STREQUAL "3.1.2"
)
    message(FATAL_ERROR "Unexpected hello response: ${hello_response}")
endif()

dnp3_json_get(shutdown_schema "${shutdown_response}" schema_version)
dnp3_json_get(shutdown_id "${shutdown_response}" id)
dnp3_json_get(shutdown_ok "${shutdown_response}" ok)
dnp3_json_get(shutdown_state "${shutdown_response}" result state)
if(
    NOT shutdown_schema STREQUAL "1"
    OR NOT shutdown_id STREQUAL "smoke-shutdown"
    OR NOT shutdown_ok
    OR NOT shutdown_state STREQUAL "SHUTTING_DOWN"
)
    message(FATAL_ERROR "Unexpected shutdown response: ${shutdown_response}")
endif()
