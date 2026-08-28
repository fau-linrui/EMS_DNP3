#pragma once

#include "dnp3host/Models.h"

#include <cstddef>
#include <istream>
#include <string>
#include <string_view>

namespace dnp3host {

class JsonLineProtocol final {
public:
    static constexpr std::size_t kMaxJsonDepth = 64;
    static constexpr std::size_t kMaxRequestIdBytes = 128;
    static constexpr std::size_t kMaxCommandBytes = 64;

    static ReadLineResult read_line(std::istream& input, std::size_t max_request_bytes);
    static ParseResult parse_request(std::string_view line);

    static Json success_response(std::string_view id, Json result);
    static Json error_response(const ProtocolError& error);
    static std::string serialize(const Json& value);
};

}  // namespace dnp3host

