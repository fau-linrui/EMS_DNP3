#pragma once

#include <nlohmann/json.hpp>

#include <cstddef>
#include <optional>
#include <string>

namespace dnp3host {

using Json = nlohmann::json;

enum class ErrorCode {
    InvalidRequest,
    RequestTooLarge,
    SchemaMismatch,
    InvalidState,
    NotConnected,
    AlreadyConnected,
    ConnectionTimeout,
    UnsupportedByBackend,
    ProcessShuttingDown,
    InternalError,
};

inline const char* to_string(const ErrorCode code) noexcept
{
    switch (code) {
    case ErrorCode::InvalidRequest:
        return "INVALID_REQUEST";
    case ErrorCode::RequestTooLarge:
        return "REQUEST_TOO_LARGE";
    case ErrorCode::SchemaMismatch:
        return "SCHEMA_MISMATCH";
    case ErrorCode::InvalidState:
        return "INVALID_STATE";
    case ErrorCode::NotConnected:
        return "NOT_CONNECTED";
    case ErrorCode::AlreadyConnected:
        return "ALREADY_CONNECTED";
    case ErrorCode::ConnectionTimeout:
        return "CONNECTION_TIMEOUT";
    case ErrorCode::UnsupportedByBackend:
        return "UNSUPPORTED_BY_BACKEND";
    case ErrorCode::ProcessShuttingDown:
        return "PROCESS_SHUTTING_DOWN";
    case ErrorCode::InternalError:
        return "INTERNAL_ERROR";
    }
    return "INTERNAL_ERROR";
}

struct Request {
    std::string id;
    std::string command;
    Json params;
};

struct ProtocolError {
    std::optional<std::string> id;
    ErrorCode code{ErrorCode::InternalError};
    std::string message;
    Json details{Json::object()};
};

struct ParseResult {
    std::optional<Request> request;
    std::optional<ProtocolError> error;
};

enum class ReadLineStatus {
    Line,
    EndOfFile,
    TooLarge,
    IoError,
};

struct ReadLineResult {
    ReadLineStatus status{ReadLineStatus::IoError};
    std::string line;
};

struct DispatchResult {
    Json response;
    bool should_stop{false};
};

}  // namespace dnp3host
