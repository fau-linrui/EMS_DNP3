#pragma once

#include "dnp3host/Backend.h"

#include <array>
#include <condition_variable>
#include <mutex>
#include <vector>

namespace dnp3host {

// This is a stack-log observation queue, not a network packet capture. Producers
// only copy bounded bytes into a preallocated ring; serialization is on RPC threads.
class ProtocolTrace final {
public:
    BackendOperationResult start(const TraceStartConfig& config);
    BackendOperationResult read(const TraceReferenceConfig& config);
    BackendOperationResult stop(const TraceReferenceConfig& config);
    Json status() const;
    bool active() const;
    void shutdown() noexcept;
    void record(std::uint64_t session_id, const char* logger,
                std::int32_t level, const char* message) noexcept;

private:
    struct Record {
        std::uint64_t sequence{0};
        std::uint64_t session_id{0};
        std::uint64_t monotonic_ns{0};
        std::array<char, 129> logger{};
        const char* level{"OTHER"};
        std::array<char, 1025> message{};
        bool message_truncated{false};
    };
    struct Snapshot {
        std::string trace_id;
        const char* state{"IDLE"};
        std::size_t capacity{16384};
        std::size_t queued{0};
        std::uint64_t dropped{0};
        std::uint64_t truncated{0};
        std::uint64_t sequence{0};
    };
    Snapshot snapshot_locked() const;
    static Json serialize(const Snapshot& snapshot);
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::vector<Record> records_;
    std::size_t head_{0};
    std::size_t queued_{0};
    std::size_t capacity_{16384};
    std::uint64_t next_trace_{0};
    std::uint64_t dropped_{0};
    std::uint64_t truncated_{0};
    std::uint64_t sequence_{0};
    std::string trace_id_;
    bool active_{false};
};

}  // namespace dnp3host
