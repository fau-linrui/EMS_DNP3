#include "dnp3host/HostController.h"
#include "dnp3host/JsonLineProtocol.h"
#include "dnp3host/ProjectInfo.h"

#include <charconv>
#include <cstddef>
#include <exception>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

std::optional<std::size_t> parse_size(const std::string_view text)
{
    std::size_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
        return std::nullopt;
    }
    return value;
}

std::optional<std::size_t> parse_arguments(const int argc, char* argv[])
{
    std::size_t max_request_bytes = dnp3host::kDefaultMaxRequestBytes;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument{argv[index]};
        std::optional<std::size_t> parsed;
        if (argument == "--max-request-bytes" && index + 1 < argc) {
            parsed = parse_size(argv[++index]);
        }
        else if (argument.rfind("--max-request-bytes=", 0) == 0) {
            parsed = parse_size(argument.substr(std::string_view{"--max-request-bytes="}.size()));
        }
        else {
            std::cerr << "invalid command-line argument\n";
            return std::nullopt;
        }

        if (!parsed || *parsed < 64
            || *parsed > dnp3host::kMaximumConfigurableRequestBytes) {
            std::cerr << "max request bytes must be between 64 and "
                      << dnp3host::kMaximumConfigurableRequestBytes << '\n';
            return std::nullopt;
        }
        max_request_bytes = *parsed;
    }
    return max_request_bytes;
}

bool configure_binary_stdio()
{
#ifdef _WIN32
    return _setmode(_fileno(stdin), _O_BINARY) != -1
        && _setmode(_fileno(stdout), _O_BINARY) != -1;
#else
    return true;
#endif
}

bool write_response(const dnp3host::Json& response)
{
    std::cout << dnp3host::JsonLineProtocol::serialize(response) << '\n';
    std::cout.flush();
    return static_cast<bool>(std::cout);
}

}  // namespace

int main(const int argc, char* argv[])
{
    const auto max_request_bytes = parse_arguments(argc, argv);
    if (!max_request_bytes) {
        return 2;
    }
    if (!configure_binary_stdio()) {
        std::cerr << "failed to configure binary standard streams\n";
        return 3;
    }

    dnp3host::HostController controller{*max_request_bytes};
    while (true) {
        auto line = dnp3host::JsonLineProtocol::read_line(std::cin, *max_request_bytes);
        if (line.status == dnp3host::ReadLineStatus::EndOfFile) {
            return 0;
        }
        if (line.status == dnp3host::ReadLineStatus::IoError) {
            std::cerr << "failed to read protocol input\n";
            return 4;
        }

        controller.record_request_received();
        if (line.status == dnp3host::ReadLineStatus::TooLarge) {
            controller.record_protocol_failure();
            const auto error = dnp3host::ProtocolError{
                std::nullopt,
                dnp3host::ErrorCode::RequestTooLarge,
                "request line exceeds the configured byte limit",
                dnp3host::Json{{"max_request_bytes", *max_request_bytes}}};
            if (!write_response(dnp3host::JsonLineProtocol::error_response(error))) {
                return 5;
            }
            continue;
        }

        auto parsed = dnp3host::JsonLineProtocol::parse_request(line.line);
        if (parsed.error) {
            controller.record_protocol_failure();
            if (!write_response(dnp3host::JsonLineProtocol::error_response(*parsed.error))) {
                return 5;
            }
            continue;
        }

        try {
            auto dispatched = controller.dispatch(*parsed.request);
            if (!write_response(dispatched.response)) {
                return 5;
            }
            if (dispatched.should_stop) {
                return 0;
            }
        }
        catch (const std::exception&) {
            controller.record_protocol_failure();
            const auto error = dnp3host::ProtocolError{
                parsed.request->id,
                dnp3host::ErrorCode::InternalError,
                "an internal error occurred while processing the request",
                dnp3host::Json::object()};
            if (!write_response(dnp3host::JsonLineProtocol::error_response(error))) {
                return 5;
            }
        }
    }
}
