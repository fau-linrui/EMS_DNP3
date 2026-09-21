"""Fault injection at subprocess, JSON, and command-result ownership boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from dnp3_master import (
    ClientStateError, CrobCommand, Dnp3MasterClient, HostProcessConfig,
    HostProtocolError, LabSafetyConfig, TcpConnectionConfig,
)
from dnp3_master._win32_job import ProcessJob
from dnp3_master.client import _ProtocolViolation, _validate_json_depth
import dnp3_master.client as client_module
import dnp3_master.models as models_module


def fake_config(mode="normal", **overrides) -> HostProcessConfig:
    return HostProcessConfig(**{
        "executable": Path(sys.executable),
        "arguments": ("-u", str(Path(__file__).with_name("fake_host.py")), "--mode", mode),
        "startup_timeout": 2.0, "request_timeout": 2.0, "shutdown_timeout": 0.2,
        **overrides,
    })


@pytest.mark.parametrize("stop_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stage", [
    "job_before_assign", "job_after_assign", "stdout_before_start",
    "stderr_after_start", "stdin_after_start", "hello_validate", "startup_copy",
])
def test_startup_interruption_reaps_process_and_partial_io_threads(
    monkeypatch: pytest.MonkeyPatch, stop_type: type[BaseException], stage: str
) -> None:
    interruption = stop_type("simulated startup cancellation")
    created = []
    original_popen = subprocess.Popen
    original_assign = ProcessJob.assign
    original_start = threading.Thread.start

    def track_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        created.append(process)
        return process

    def interrupt_assignment(job, pid):
        if stage == "job_before_assign":
            raise interruption
        original_assign(job, pid)
        if stage == "job_after_assign":
            raise interruption

    def interrupt_thread_start(thread):
        if stage == "stdout_before_start" and thread.name.startswith("dnp3-host-stdout-"):
            raise interruption
        original_start(thread)
        if (stage == "stderr_after_start" and thread.name.startswith("dnp3-host-stderr-")) or (
            stage == "stdin_after_start" and thread.name.startswith("dnp3-host-stdin-")
        ):
            raise interruption

    monkeypatch.setattr(subprocess, "Popen", track_process)
    monkeypatch.setattr(ProcessJob, "assign", interrupt_assignment)
    monkeypatch.setattr(threading.Thread, "start", interrupt_thread_start)
    client = Dnp3MasterClient(fake_config())
    if stage == "hello_validate":
        def interrupt_validation(_result):
            raise interruption
        monkeypatch.setattr(client, "_validate_hello", interrupt_validation)
    if stage == "startup_copy":
        original_copy = client_module.deepcopy

        def interrupt_copy(value, *args, **kwargs):
            if isinstance(value, dict) and "host_version" in value:
                raise interruption
            return original_copy(value, *args, **kwargs)

        monkeypatch.setattr(client_module, "deepcopy", interrupt_copy)
    try:
        with pytest.raises(stop_type) as raised:
            with client:
                pytest.fail("__enter__ must propagate the interruption")
        assert raised.value is interruption
        assert len(created) == 1
        process = created[0]
        assert process.poll() is not None
        assert not client.is_running
        assert client._job is None
        assert client.diagnostics.returncode is not None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert not any(
            thread.name.startswith("dnp3-host-") and thread.name.endswith(f"-{process.pid}")
            for thread in threading.enumerate()
        )
        with pytest.raises(ClientStateError):
            client.start()
    finally:
        client.close()
        # A failing test must still own the fixture process and never leave it
        # alive; this fallback is not counted as successful implementation cleanup.
        for process in created:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


@pytest.mark.parametrize("depth", [0, 1, 63])
def test_ndjson_response_nesting_including_envelope_accepts_64(depth: int) -> None:
    with Dnp3MasterClient(fake_config()) as client:
        result = client.request("nested_response", {"depth": depth})
        for _ in range(depth):
            result = result[0]
        assert result == 0
        assert client.is_running


@pytest.mark.parametrize("depth", [64, 20000])
def test_deep_ndjson_response_is_protocol_error_and_destroys_host(depth: int) -> None:
    with Dnp3MasterClient(fake_config()) as client:
        with pytest.raises(HostProtocolError, match="nesting exceeds 64"):
            client.request("nested_response", {"depth": depth})
        assert not client.is_running
        assert client.diagnostics.returncode is not None


@pytest.mark.parametrize("text", [
    "[{}]" * 2000,
    '\\"' * 2000,
    '"' + "\\" * 2000 + "[[[{{{",
    "\u6d4b\u8bd5" + "[]" * 2000,
    "line\n" + "[]" * 2000,
])
def test_json_depth_ignores_escaped_quoted_brackets(text: str) -> None:
    encoded = json.dumps({"value": text})
    _validate_json_depth(encoded)
    assert json.loads(encoded)["value"] == text


def test_json_depth_does_not_let_escapes_hide_real_nesting() -> None:
    encoded = '{"text":"\\\\\\\"[{{", "nested":' + "[" * 64 + "0" + "]" * 64 + "}"
    with pytest.raises(_ProtocolViolation, match="nesting"):
        _validate_json_depth(encoded)


@pytest.mark.parametrize("simulator", [False, True], ids=["lab", "simulator"])
@pytest.mark.parametrize("fault", ["deep_response", "parser_recursion", "copy_recursion"])
def test_control_response_recursion_uses_existing_uncertain_result_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, simulator: bool, fault: str
) -> None:
    directory = tmp_path / "incidents"
    config = fake_config("tcp_api", safety_incident_directory=directory)
    connection = TcpConnectionConfig(
        host="192.0.2.10", simulator=simulator,
        safety=None if simulator else LabSafetyConfig("test-operator", "test-dut", True),
    )
    with Dnp3MasterClient(config) as client:
        client.connect(connection)
        if fault == "parser_recursion":
            original_loads = client_module.json.loads

            def broken_parser(text, *args, **kwargs):
                if '"point_results"' in text:
                    raise RecursionError("simulated JSON decoder stack exhaustion")
                return original_loads(text, *args, **kwargs)

            monkeypatch.setattr(client_module.json, "loads", broken_parser)
        elif fault == "copy_recursion":
            original_copy = models_module.deepcopy

            def broken_copy(value, *args, **kwargs):
                if isinstance(value, dict) and "request_ordinal" in value:
                    raise RecursionError("simulated result deepcopy stack exhaustion")
                return original_copy(value, *args, **kwargs)

            monkeypatch.setattr(models_module, "deepcopy", broken_copy)
        index = 65527 if fault == "deep_response" else 0
        with pytest.raises(HostProtocolError) as raised:
            client.direct_operate([CrobCommand(index, "latch_on")], timeout=0.25)
        assert not client.is_running
        assert not client.state_change_authorized
        assert client.diagnostics.returncode is not None
        assert raised.value.details["persistent_safety_lock"] is not simulator
        if simulator:
            assert raised.value.details["required_action"] == "START_NEW_SESSION"
            assert not directory.exists()
        else:
            incident = client.active_safety_incident()
            assert incident is not None
            assert incident["incident_id"] == raised.value.incident_id
        with pytest.raises(ClientStateError):
            client.direct_operate([CrobCommand(0, "latch_off")])


def test_copy_recursion_in_read_result_is_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Dnp3MasterClient(fake_config("tcp_api")) as client:
        client.connect(TcpConnectionConfig(host="192.0.2.10"))
        original_copy = models_module.deepcopy

        def broken_copy(value, *args, **kwargs):
            if isinstance(value, dict) and "fragments_received" in value:
                raise RecursionError("simulated read result deepcopy failure")
            return original_copy(value, *args, **kwargs)

        monkeypatch.setattr(models_module, "deepcopy", broken_copy)
        with pytest.raises(HostProtocolError, match="result is invalid"):
            client.integrity_poll(timeout=0.25)
        assert not client.is_running


@pytest.mark.parametrize("stage", ["startup_copy", "hello_info", "connect_copy"])
def test_public_result_copy_recursion_also_destroys_host(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    client = Dnp3MasterClient(fake_config("tcp_api"))
    original_copy = client_module.deepcopy

    def broken_copy(value, *args, **kwargs):
        if isinstance(value, dict) and (
            (stage == "connect_copy" and value.get("state") == "CONNECTED")
            or (stage != "connect_copy" and "host_version" in value)
        ):
            raise RecursionError("simulated public result deepcopy failure")
        return original_copy(value, *args, **kwargs)

    try:
        if stage != "startup_copy":
            client.start()
        monkeypatch.setattr(client_module, "deepcopy", broken_copy)
        with pytest.raises(HostProtocolError, match="response copy exceeded"):
            if stage == "startup_copy":
                client.start()
            elif stage == "hello_info":
                _ = client.hello_info
            else:
                client.connect(TcpConnectionConfig(host="192.0.2.10", simulator=True))
        assert not client.is_running
        assert not client.state_change_authorized
        assert client.diagnostics.returncode is not None
    finally:
        client.close()
