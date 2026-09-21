// Compile the private implementation into this dedicated test translation
// unit so callback ordering can be driven deterministically. No production
// test hook or public API is added; this target does not link a second copy
// of OpenDnp3ReadSupport.cpp from the static core library.
#include "../src/OpenDnp3ReadSupport.cpp"

#include <iostream>
#include <string_view>

namespace {

int failures = 0;

void check(const bool condition, const std::string_view message)
{
    if (!condition) {
        ++failures;
        std::cerr << "FAILED: " << message << '\n';
    }
}

struct ReadFixture {
    ReadFixture()
        : iin(std::make_shared<dnp3host::IinStore>()),
          gate(std::make_shared<dnp3host::CompletionGate>()),
          application(iin, gate),
          operation(std::make_shared<dnp3host::ReadOperation>(
              7, options(), std::make_shared<std::atomic<std::uint64_t>>(0),
              iin->last_sequence(), nullptr))
    {
        gate->set_active(operation);
        operation->mark_started();
    }

    static dnp3host::ReadOptions options()
    {
        dnp3host::ReadOptions value;
        value.timeout_ms = 50;
        return value;
    }

    std::vector<dnp3host::IinObservation> observations()
    {
        return iin->after(operation->iin_start_sequence(), dropped, window_dropped);
    }

    void response_iin()
    {
        application.OnReceiveIIN(opendnp3::IINField(opendnp3::IINBit::OBJECT_UNKNOWN));
    }

    std::shared_ptr<dnp3host::IinStore> iin;
    std::shared_ptr<dnp3host::CompletionGate> gate;
    dnp3host::TrackingMasterApplication application;
    std::shared_ptr<dnp3host::ReadOperation> operation;
    std::uint64_t dropped{0};
    std::uint64_t window_dropped{0};
};

void check_timeout(const dnp3host::BackendOperationResult& result)
{
    check(result.error && result.error->code == dnp3host::ErrorCode::ResponseTimeout,
          "an expired wait must not be upgraded by later completion");
    if (result.error) {
        check(result.error->details.at("task_id") == 7, "timeout must retain task identity");
        check(result.error->details.at("timeout_ms") == 50, "timeout must retain its configured bound");
        check(result.error->details.at("task_started") == true, "timeout must retain submission state");
        check(!result.error->details.contains("operation_result"),
              "expired wait must not publish a successful result with a stale IIN snapshot");
    }
}

void test_timeout_then_late_response(const opendnp3::TaskCompletion completion)
{
    ReadFixture fixture;
    fixture.operation->wait_for_completion();
    const auto stale_observations = fixture.observations();
    check(stale_observations.empty(), "pre-response snapshot must be empty");

    // Exact problematic ordering, without a scheduler race: wait expires,
    // caller snapshots IIN, then the stack completes and records final IIN.
    fixture.operation->mark_complete(completion);
    fixture.operation->mark_destroyed();
    check(!fixture.operation->is_done(), "response completion must still wait for its IIN");
    fixture.response_iin();
    check(fixture.operation->is_done(), "late callback must release the active underlying task");
    check_timeout(fixture.operation->outcome(
        stale_observations, fixture.dropped, fixture.window_dropped));

    const auto final_observations = fixture.observations();
    check(final_observations.size() == 1, "late IIN must still reach the bounded store");
    check_timeout(fixture.operation->outcome(
        final_observations, fixture.dropped, fixture.window_dropped));
}

void test_timeout_between_completion_and_iin()
{
    ReadFixture fixture;
    fixture.operation->mark_complete(opendnp3::TaskCompletion::SUCCESS);
    fixture.operation->mark_destroyed();
    check(!fixture.operation->is_done(), "success callback alone is not complete without IIN");
    fixture.operation->wait_for_completion();
    const auto stale_observations = fixture.observations();
    fixture.response_iin();
    check_timeout(fixture.operation->outcome(
        stale_observations, fixture.dropped, fixture.window_dropped));
}

void test_on_time_response_preserves_iin()
{
    ReadFixture fixture;
    fixture.operation->mark_complete(opendnp3::TaskCompletion::SUCCESS);
    fixture.operation->mark_destroyed();
    fixture.response_iin();
    fixture.operation->wait_for_completion();
    const auto observations = fixture.observations();
    const auto result = fixture.operation->outcome(
        observations, fixture.dropped, fixture.window_dropped);
    check(!result.error, "on-time successful task must still return a read result");
    if (!result.error) {
        check(result.result.at("task_status") == "SUCCESS", "normal task status must be retained");
        check(result.result.at("iin").at("raw_hex") == "0002", "request-error IIN must be preserved");
        check(result.result.at("iin").at("bits") == dnp3host::Json::array({"IIN2.1.OBJECT_UNKNOWN"}),
              "OBJECT_UNKNOWN must remain available for caller rejection");
        check(result.result.at("iin").at("observations").size() == 1,
              "on-time final IIN observation must not be omitted");
    }
}

void test_cancellation_respects_wait_decision()
{
    ReadFixture late;
    late.operation->wait_for_completion();
    late.operation->cancel();
    const auto observations = late.observations();
    check(late.operation->is_done(), "cancellation must release a timed-out task");
    check_timeout(late.operation->outcome(observations, late.dropped, late.window_dropped));

    ReadFixture on_time;
    on_time.operation->cancel();
    on_time.operation->wait_for_completion();
    const auto cancelled_observations = on_time.observations();
    const auto cancelled = on_time.operation->outcome(
        cancelled_observations, on_time.dropped, on_time.window_dropped);
    check(cancelled.error && cancelled.error->code == dnp3host::ErrorCode::TaskFailed,
          "cancellation before the wait deadline must retain its task failure");
}

}  // namespace

int main()
{
    test_timeout_then_late_response(opendnp3::TaskCompletion::SUCCESS);
    test_timeout_then_late_response(opendnp3::TaskCompletion::FAILURE_BAD_RESPONSE);
    test_timeout_between_completion_and_iin();
    test_on_time_response_preserves_iin();
    test_cancellation_respects_wait_decision();
    if (failures != 0) {
        std::cerr << failures << " read completion test(s) failed\n";
        return 1;
    }
    std::cout << "read completion tests passed\n";
    return 0;
}
