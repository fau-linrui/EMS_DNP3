from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def host_executable() -> Path:
    configured = os.environ.get("DNP3_MASTER_HOST_EXE")
    assert configured, "DNP3_MASTER_HOST_EXE must identify the built native host"
    executable = Path(configured)
    assert executable.is_file(), f"native host does not exist: {executable}"
    return executable


def run_host(
    executable: Path,
    protocol_input: bytes,
    *arguments: str,
) -> list[dict[str, object]]:
    process = subprocess.Popen(
        [str(executable), *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(protocol_input, timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        pytest.fail("native host did not terminate after stdin reached EOF")

    assert process.returncode == 0
    assert stderr == b"", stderr.decode("utf-8", errors="replace")
    lines = [line for line in stdout.splitlines() if line]
    return [json.loads(line.decode("utf-8")) for line in lines]


def request(request_id: str, command: str, params: object | None = None) -> bytes:
    envelope = {
        "schema_version": 1,
        "id": request_id,
        "cmd": command,
        "params": {} if params is None else params,
    }
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n"


def test_hello_status_and_shutdown(host_executable: Path) -> None:
    responses = run_host(
        host_executable,
        request("hello-1", "hello")
        + request("status-1", "get_status")
        + request("shutdown-1", "shutdown"),
    )

    assert len(responses) == 3
    hello = responses[0]
    assert hello["ok"] is True
    assert hello["id"] == "hello-1"
    result = hello["result"]
    assert isinstance(result, dict)
    assert result["backend"] == "opendnp3"
    assert result["backend_version"] == "3.1.2"
    assert result["platform"] == "windows-x64"
    assert result["capability_matrix_version"] == "1"
    assert result["supported_commands"] == [
        "connect",
        "disconnect",
        "get_status",
        "hello",
        "shutdown",
        "wait_event",
    ]
    assert set(result["capabilities"]) == {
        "CHANNEL.TCP.CLIENT",
        "CHANNEL.RECONNECT",
    }
    assert len(result["capability_matrix_sha256"]) == 64

    status = responses[1]
    assert status["ok"] is True
    assert status["result"]["state"] == "READY"
    assert status["result"]["channel"]["session_active"] is False
    assert status["result"]["channel"]["state"] == "CLOSED"
    assert status["result"]["metrics"]["requests_received"] == 2

    shutdown = responses[2]
    assert shutdown == {
        "schema_version": 1,
        "id": "shutdown-1",
        "ok": True,
        "result": {"state": "SHUTTING_DOWN"},
    }


@pytest.mark.parametrize(
    ("protocol_input", "reason"),
    [
        (b"\n", "empty_line"),
        (b"{\n", "invalid_json"),
        (b"\xff\n", "invalid_utf8"),
        (
            b'{"schema_version":1,"id":"a","id":"b",'
            b'"cmd":"hello","params":{}}\n',
            "duplicate_key",
        ),
    ],
)
def test_invalid_input_has_stable_error(
    host_executable: Path,
    protocol_input: bytes,
    reason: str,
) -> None:
    [response] = run_host(host_executable, protocol_input)
    assert response["ok"] is False
    assert response["error"]["code"] == "INVALID_REQUEST"
    assert response["error"]["details"]["reason"] == reason


def test_schema_mismatch_echoes_valid_id(host_executable: Path) -> None:
    invalid = (
        b'{"schema_version":2,"id":"schema-1","cmd":"hello","params":{}}\n'
    )
    [response] = run_host(host_executable, invalid)
    assert response["id"] == "schema-1"
    assert response["error"]["code"] == "SCHEMA_MISMATCH"
    assert response["error"]["details"] == {"expected": 1, "received": 2}


def test_duplicate_request_id_is_rejected(host_executable: Path) -> None:
    responses = run_host(
        host_executable,
        request("same-id", "hello") + request("same-id", "get_status"),
    )
    assert responses[0]["ok"] is True
    assert responses[1]["error"]["code"] == "INVALID_REQUEST"
    assert responses[1]["error"]["details"]["reason"] == "duplicate_id"


def test_invalid_unavailable_and_unknown_commands_are_distinct(
    host_executable: Path,
) -> None:
    responses = run_host(
        host_executable,
        request("connect-1", "connect")
        + request("read-1", "read")
        + request("unknown-1", "no_such_command"),
    )
    assert responses[0]["error"]["code"] == "INVALID_REQUEST"
    assert responses[0]["error"]["details"] == {
        "field": "host",
        "reason": "missing_field",
    }
    assert responses[1]["error"]["code"] == "UNSUPPORTED_BY_BACKEND"
    assert responses[1]["error"]["details"]["backend"] == "opendnp3"
    assert responses[2]["error"]["code"] == "INVALID_REQUEST"
    assert responses[2]["error"]["details"]["reason"] == "unknown_command"


def test_crlf_is_accepted(host_executable: Path) -> None:
    protocol_input = request("crlf-1", "hello").replace(b"\n", b"\r\n")
    [response] = run_host(host_executable, protocol_input)
    assert response["ok"] is True


def test_oversized_line_is_bounded_and_reader_resynchronizes(
    host_executable: Path,
) -> None:
    oversized = b"x" * 129 + b"\n"
    responses = run_host(
        host_executable,
        oversized + request("shutdown-after-large", "shutdown"),
        "--max-request-bytes=128",
    )
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == "REQUEST_TOO_LARGE"
    assert responses[0]["error"]["details"]["max_request_bytes"] == 128
    assert responses[1]["ok"] is True


def test_each_stdout_line_is_one_json_object(host_executable: Path) -> None:
    responses = run_host(
        host_executable,
        request("one", "hello") + request("two", "get_status"),
    )
    assert [response["id"] for response in responses] == ["one", "two"]
    assert all(response["schema_version"] == 1 for response in responses)
