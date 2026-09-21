from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

from dnp3_master import (
    ClientStateError,
    Dnp3MasterClient,
    HostProcessConfig,
    HostProtocolError,
    TraceConfig,
    TraceIncompleteError,
)


def summary(**overrides):
    result = {
        "trace_id": "trace-1", "state": "ACTIVE", "scope": "opendnp3_stack",
        "queue_capacity": 16384, "queued_records": 0, "dropped_records": 0,
        "truncated_records": 0, "last_sequence": 0, "complete": True,
    }
    result.update(overrides)
    return result


@pytest.fixture
def trace_client(monkeypatch):
    client = Dnp3MasterClient(HostProcessConfig(executable=Path(sys.executable)))
    client._hello_info = {"supported_commands": ["trace.start", "trace.read", "trace.stop"]}
    calls = []
    replies = []
    aborted = []

    def request(command, params=None, *, timeout=None):
        calls.append((command, params, timeout))
        return deepcopy(replies.pop(0))

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "_abort_process", aborted.append)
    return client, calls, replies, aborted


def test_trace_typed_api_lifecycle_and_default_timeout(trace_client):
    client, calls, replies, aborted = trace_client
    replies.extend([
        summary(),
        dict(summary(), records=[], timed_out=True),
        summary(state="STOPPED"),
        dict(summary(state="STOPPED"), records=[], timed_out=True),
    ])
    assert client.start_trace().trace_id == "trace-1"
    assert client.read_trace(timeout=20).complete
    assert client.stop_trace().state == "STOPPED"
    assert client.read_trace().summary.queued_records == 0
    assert calls[0][1] == {"queue_capacity": 16384}
    assert calls[1][1] == {"trace_id": "trace-1", "max_records": 256, "timeout_ms": 20000}
    assert calls[1][2] == 21.0
    assert calls[2][1] == {"trace_id": "trace-1"}
    assert not aborted


def test_old_host_trace_is_explicitly_unsupported_but_not_aborted(trace_client):
    client, calls, _, aborted = trace_client
    client._hello_info = {"supported_commands": ["hello", "shutdown"]}
    with pytest.raises(ClientStateError, match="matching native host"):
        client.start_trace()
    assert not calls and not aborted


@pytest.mark.parametrize("method", ["read_trace", "stop_trace"])
def test_trace_requires_start(trace_client, method):
    client, calls, _, _ = trace_client
    with pytest.raises(ClientStateError, match="start_trace"):
        getattr(client, method)()
    assert not calls


@pytest.mark.parametrize("params", [
    {"max_records": True}, {"max_records": 0}, {"max_records": 1025},
    {"max_records": 1.5}, {"timeout": True}, {"timeout": -1},
    {"timeout": 61}, {"timeout": float("nan")}, {"timeout": float("inf")},
    {"timeout": "1"}, {"require_complete": 1},
])
def test_trace_invalid_arguments_do_not_exchange(trace_client, params):
    client, calls, _, _ = trace_client
    with pytest.raises((TypeError, ValueError)):
        client.read_trace(**params)
    assert not calls


@pytest.mark.parametrize("command", ["trace.start", "trace.read", "trace.stop"])
def test_trace_raw_api_cannot_bypass_decoder_state(trace_client, command):
    client, calls, _, _ = trace_client
    with pytest.raises(ClientStateError, match="typed"):
        client.request(command)
    assert not calls


def test_trace_overflow_raises_with_partial_batch_without_aborting(trace_client):
    client, _, replies, aborted = trace_client
    lost = summary(dropped_records=1, last_sequence=1, complete=False)
    replies.extend([summary(), dict(lost, records=[], timed_out=True),
                    dict(lost, records=[], timed_out=True)])
    client.start_trace()
    with pytest.raises(TraceIncompleteError) as failure:
        client.read_trace()
    assert failure.value.batch.summary.dropped_records == 1
    assert not client.read_trace(require_complete=False).complete
    assert not aborted


@pytest.mark.parametrize("changes", [
    {"trace_id": "trace-2"}, {"queue_capacity": 1},
    {"complete": "true"}, {"unexpected": 0},
])
def test_trace_malformed_result_fails_protocol(trace_client, changes):
    client, _, replies, aborted = trace_client
    replies.extend([summary(), dict(summary(**changes), records=[], timed_out=True)])
    client.start_trace()
    with pytest.raises(HostProtocolError, match="trace.read"):
        client.read_trace()
    assert aborted


def test_trace_rejects_regressing_snapshot_counters(trace_client):
    client, _, replies, aborted = trace_client
    replies.extend([
        summary(), summary(state="STOPPED", last_sequence=2, dropped_records=2, complete=False),
        dict(summary(state="STOPPED"), records=[], timed_out=True),
    ])
    client.start_trace()
    client.stop_trace()
    with pytest.raises(HostProtocolError):
        client.read_trace(require_complete=False)
    assert aborted


def test_new_trace_resets_decoder_after_stopped_trace(trace_client):
    client, _, replies, aborted = trace_client
    replies.extend([
        summary(queue_capacity=8),
        dict(summary(queue_capacity=8, last_sequence=1, dropped_records=1, complete=False),
             records=[], timed_out=True),
        summary(queue_capacity=8, state="STOPPED", last_sequence=1, dropped_records=1, complete=False),
        summary(trace_id="trace-2"),
        dict(summary(trace_id="trace-2"), records=[], timed_out=True),
    ])
    client.start_trace(TraceConfig(queue_capacity=8))
    assert not client.read_trace(require_complete=False).complete
    client.stop_trace()
    client.start_trace()
    assert client.read_trace().complete
    assert not aborted
