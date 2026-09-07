from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dnp3_master import Dnp3MasterClient, run_soak
from dnp3_master.process_metrics import ProcessResourceSample
from test_performance import _profile


@pytest.mark.parametrize("fault, expected", [
    ("none", "COMPLETED"),
    ("exit", "INCOMPLETE_HOST_EXIT"),
    ("stop", "INCOMPLETE_INTERRUPTED"),
    ("terminal_exit", "INCOMPLETE_HOST_EXIT"),
    ("terminal_stop", "INCOMPLETE_INTERRUPTED"),
    ("terminal_closed", "INCOMPLETE_CONNECTION_LIMIT"),
    ("terminal_event_drop", "INCOMPLETE_CONNECTION_LIMIT"),
])
def test_soak_terminal_interval_must_be_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str, expected: str,
) -> None:
    base = _profile(1)
    profile = replace(base, scenarios=(base.scenarios[0],), soak=replace(
        base.soak, target_duration_seconds=0.3, cycle_interval_seconds=0.1,
    ))
    client = Mock(spec=Dnp3MasterClient)
    client.pid = 999999
    client.is_running = True
    client.hello_info = {"host_version": "synthetic-soak"}
    client.wait_event.return_value = {"events": [], "remaining": 0, "dropped_total": 0}
    clock = [0.0]
    stopped = [False]

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        if clock[0] >= 0.3:
            if fault == "exit":
                client.is_running = False
            if fault == "stop":
                stopped[0] = True

    def status(**kwargs: object) -> dict[str, object]:
        if clock[0] >= 0.3:
            if fault == "terminal_exit":
                client.is_running = False
            if fault == "terminal_stop":
                stopped[0] = True
            if fault == "terminal_closed":
                return {"channel": {"state": "CLOSED"}}
            if fault == "terminal_event_drop":
                client.wait_event.return_value = {"events": [], "remaining": 0, "dropped_total": 1}
        return {"channel": {"state": "OPEN"}}

    client.get_status.side_effect = status
    sampler = Mock()
    sampler.sample.side_effect = lambda: ProcessResourceSample(
        monotonic_ns=round(clock[0] * 1e9), process_cpu_seconds=clock[0] * 0.1,
        working_set_bytes=1000, private_bytes=1000, handle_count=10, thread_count=1,
    )
    monkeypatch.setattr("dnp3_master.soak.execute_read_only_iteration", lambda *args: (
        SimpleNamespace(timings={"duration_ms": 1.0}), None,
    ))
    report = run_soak(
        client, profile, tmp_path, sampler=sampler,
        stop_requested=lambda: stopped[0], monotonic=lambda: clock[0], sleep=sleep,
    )
    assert report["status"] == expected
    assert report["passed"] is (fault == "none")
    persisted = json.loads(Path(report["final_report_path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == expected
    assert persisted["passed"] is (fault == "none")
