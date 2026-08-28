"""Controllable NDJSON child process used only by process-management tests."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any


def write_json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def success(request_id: str, result: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def error(request_id: str, code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": request_id,
        "ok": False,
        "error": {
            "code": code,
            "message": "controlled fake-host error",
            "details": {"source": "fake_host"},
        },
    }


def hello_result(mode: str = "normal") -> dict[str, Any]:
    tcp_api = mode == "tcp_api"
    return {
        "host_version": "test",
        "backend": "opendnp3" if tcp_api else "none",
        "backend_version": "3.1.2" if tcp_api else None,
        "git_commit": "test",
        "platform": "windows-x64",
        "capability_matrix_version": "test",
        "capability_matrix_sha256": "0" * 64,
        "supported_commands": (
            ["connect", "disconnect", "get_status", "hello", "shutdown", "wait_event"]
            if tcp_api
            else ["get_status", "hello", "shutdown"]
        ),
        "capabilities": (
            {
                "CHANNEL.TCP.CLIENT": {
                    "status": "IMPLEMENTED_UNVERIFIED",
                    "implementation_revision": "fake-test",
                }
            }
            if tcp_api
            else {}
        ),
        "limits": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()

    first_request = True
    connected = False
    for line in sys.stdin:
        request = json.loads(line)
        request_id = request["id"]
        command = request["cmd"]

        if first_request:
            first_request = False
            if args.mode == "silent":
                time.sleep(60)
            if args.mode == "exit_23":
                sys.stderr.write("controlled-exit-23\n")
                sys.stderr.flush()
                return 23
            if args.mode == "invalid_json":
                sys.stdout.write("this is not json\n")
                sys.stdout.flush()
                continue
            if args.mode == "wrong_id":
                write_json(success("wrong-response-id", hello_result(args.mode)))
                continue
            if args.mode == "schema_v2":
                response = success(request_id, hello_result(args.mode))
                response["schema_version"] = 2
                write_json(response)
                continue
            if args.mode == "duplicate_key":
                sys.stdout.write(
                    '{"schema_version":1,"id":"'
                    + request_id
                    + '","id":"duplicate","ok":true,"result":{}}\n'
                )
                sys.stdout.flush()
                continue
            if args.mode == "stderr_flood":
                sys.stderr.write("E" * 200_000)
                sys.stderr.flush()
            if args.mode == "stdout_flood":
                for _ in range(1000):
                    write_json(success(request_id, hello_result(args.mode)))
                time.sleep(60)

        if command == "hello":
            write_json(success(request_id, hello_result(args.mode)))
            if args.mode == "exit_after_hello":
                sys.stderr.write("controlled-exit-after-hello\n")
                sys.stderr.flush()
                return 37
        elif command == "get_status":
            if args.mode == "hang_on_status":
                time.sleep(60)
            write_json(
                success(
                    request_id,
                    {
                        "state": "CONNECTED" if connected else "READY",
                        "backend": "opendnp3" if args.mode == "tcp_api" else "none",
                        "metrics": {},
                    },
                )
            )
        elif command == "shutdown":
            if args.mode == "ignore_shutdown":
                time.sleep(60)
            write_json(success(request_id, {"state": "SHUTTING_DOWN"}))
            return 0
        elif command == "connect":
            if args.mode != "tcp_api":
                write_json(error(request_id, "UNSUPPORTED_BY_BACKEND"))
            elif connected:
                write_json(error(request_id, "ALREADY_CONNECTED"))
            else:
                connected = True
                write_json(
                    success(
                        request_id,
                        {
                            "state": "CONNECTED",
                            "channel_state": "OPEN",
                            "session_id": 1,
                            "received": request["params"],
                        },
                    )
                )
        elif command == "disconnect" and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            else:
                connected = False
                write_json(
                    success(
                        request_id,
                        {"state": "READY", "channel_state": "SHUTDOWN"},
                    )
                )
        elif command == "wait_event" and args.mode == "tcp_api":
            write_json(
                success(
                    request_id,
                    {
                        "events": [],
                        "timed_out": True,
                        "remaining": 0,
                        "dropped_total": 0,
                        "received": request["params"],
                    },
                )
            )
        else:
            write_json(error(request_id, "INVALID_REQUEST"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
