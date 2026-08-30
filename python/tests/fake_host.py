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


def error(
    request_id: str,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error_details = {"source": "fake_host"}
    if details is not None:
        error_details.update(details)
    return {
        "schema_version": 1,
        "id": request_id,
        "ok": False,
        "error": {
            "code": code,
            "message": "controlled fake-host error",
            "details": error_details,
        },
    }


def hello_result(mode: str = "normal") -> dict[str, Any]:
    tcp_api = mode in {"tcp_api", "hello_backend_mismatch"}
    return {
        "host_version": "0.5.1" if mode != "hello_version_mismatch" else "9.9.9",
        "backend": "opendnp3" if tcp_api else "none",
        "backend_version": (
            "9.9.9" if mode == "hello_backend_mismatch" else "3.1.2"
        ) if tcp_api else None,
        "git_commit": "test",
        "platform": "windows-x64",
        "capability_matrix_version": "test",
        "capability_matrix_sha256": (
            "f" * 64 if mode == "hello_matrix_mismatch" else "0" * 64
        ),
        "supported_commands": (
            [
                "class_poll",
                "connect",
                "direct_operate",
                "disable_unsolicited",
                "disconnect",
                "enable_unsolicited",
                "get_status",
                "hello",
                "integrity_poll",
                "read",
                "select_and_operate",
                "shutdown",
                "stats",
                "wait_event",
                "wait_unsolicited",
            ]
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


def read_result(params: dict[str, Any]) -> dict[str, Any]:
    detail = params.get("return_mode", "detail") == "detail"
    measurements = (
        [
            {
                "receive_seq": 1,
                "received_monotonic_ns": 100,
                "kind": "analog_input",
                "group": 30,
                "variation": 5,
                "qualifier": "UINT16_START_STOP",
                "qualifier_raw": 1,
                "index": 7,
                "value": 220.5,
                "flags_raw": 1,
                "flags_valid": True,
                "dnp3_timestamp_ms": None,
                "timestamp_quality": "INVALID",
                "is_event": False,
                "header_index": 0,
                "source": "solicited",
                "fragment_index": 0,
            }
        ]
        if detail
        else []
    )
    return {
        "task_id": 1,
        "task_status": "SUCCESS",
        "task_started": True,
        "task_destroyed": True,
        "return_mode": params.get("return_mode", "detail"),
        "measurements": measurements,
        "summary": {
            "received_total": 1,
            "stored_detail": len(measurements),
            "overflow": 0,
            "fragments_received": 1,
            "fragments_stored": 1,
            "fragment_overflow": 0,
            "max_fragments": 4096,
            "max_measurements": params.get("max_measurements", 10_000),
            "by_kind": {"analog_input": 1},
        },
        "fragments": [
            {
                "fragment_index": 0,
                "source": "solicited",
                "fir": True,
                "fin": True,
                "ended": True,
            }
        ],
        "iin": {
            "lsb": 0,
            "msb": 0,
            "raw_hex": "0000",
            "bits": [],
            "observations": [],
            "observation_store_dropped_total": 0,
            "observation_window_dropped": 0,
            "observation_store_capacity": 1024,
        },
        "timings": {"duration_ms": 1.0},
        "received": params,
    }


def command_result(command: str, params: dict[str, Any]) -> dict[str, Any]:
    point_results = []
    for ordinal, requested in enumerate(params["commands"]):
        point_results.append(
            {
                "header_index": ordinal,
                "index": requested["index"],
                "state": "SUCCESS",
                "state_raw": 5,
                "status": "SUCCESS",
                "status_raw": 0,
                "status_edition": "IEEE1815-2012",
                "status_backend": "SUCCESS",
                "status_reserved_2012": False,
                "status_wire_raw_unambiguous": True,
                "requested": {
                    "request_ordinal": ordinal,
                    **requested,
                },
            }
        )
    return {
        "task_id": 2,
        "mode": command,
        "response_mode": "response",
        "task_status": "SUCCESS",
        "task_callback_status": "SUCCESS",
        "task_started": True,
        "task_destroyed": True,
        "all_success": True,
        "execution_uncertain": False,
        "point_results": point_results,
        "summary": {
            "requested_points": len(point_results),
            "returned_points": len(point_results),
            "successful_points": len(point_results),
            "failed_points": 0,
            "by_status": {"SUCCESS": len(point_results)},
            "by_state": {"SUCCESS": len(point_results)},
        },
        "timings": {"duration_ms": 1.0},
    }


def unsolicited_control_result(
    action: str, params: dict[str, Any]
) -> dict[str, Any]:
    return {
        "task_id": 1_000_001,
        "task_status": "SUCCESS",
        "task_started": True,
        "task_destroyed": True,
        "action": action,
        "classes": params["classes"],
        "timings": {"duration_ms": 1.0},
    }


def unsolicited_batch(enabled_classes: set[int]) -> dict[str, Any]:
    classes = sorted(enabled_classes)
    measurements = (
        [
            {
                "receive_seq": 1,
                "received_monotonic_ns": 200,
                "session_id": 1,
                "kind": "binary_input",
                "group": 2,
                "variation": 2,
                "qualifier": "UINT16_CNT_UINT16_INDEX",
                "qualifier_raw": 40,
                "index": 7,
                "value": True,
                "flags_raw": 1,
                "flags_valid": True,
                "dnp3_timestamp_ms": 1_700_000_000_101,
                "timestamp_quality": "SYNCHRONIZED",
                "is_event": True,
                "header_index": 0,
                "source": "unsolicited",
                "fragment_index": 1,
            }
        ]
        if enabled_classes
        else []
    )
    return {
        "session_id": 1,
        "enabled": bool(classes),
        "classes": classes,
        "measurements": measurements,
        "timed_out": not measurements,
        "summary": {
            "returned": len(measurements),
            "remaining": 0,
            "received_total": len(measurements),
            "dropped_total": 0,
            "queue_capacity": 4096,
            "last_receive_seq": len(measurements),
            "fragments_total": int(bool(measurements)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()

    first_request = True
    connected = False
    safety_token: str | None = None
    unsolicited_classes: set[int] = set()
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
        elif command == "stats":
            write_json(
                success(
                    request_id,
                    {
                        "scope": "host_channel_and_local_queues",
                        "host": {"requests_received": 1},
                        "channel": {"session_active": connected},
                        "safety": {"state_change_authorized": safety_token is not None},
                        "limitations": [],
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
                unsolicited_classes.clear()
                safety = request["params"].get("safety")
                authorized = bool(
                    isinstance(safety, dict)
                    and safety.get("environment") == "LAB"
                    and safety.get("allow_state_change") is True
                )
                safety_token = "0123456789abcdef0123456789abcdef" if authorized else None
                write_json(
                    success(
                        request_id,
                        {
                            "state": "CONNECTED",
                            "channel_state": "OPEN",
                            "session_id": 1,
                            "received": request["params"],
                            "safety": {
                                "environment": "LAB" if authorized else "UNSPECIFIED",
                                "state_change_authorized": authorized,
                                "safety_token": safety_token,
                                "expires_on": "disconnect_or_process_exit",
                            },
                        },
                    )
                )
        elif command == "disconnect" and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            else:
                connected = False
                safety_token = None
                unsolicited_classes.clear()
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
        elif command in {"enable_unsolicited", "disable_unsolicited"} and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            else:
                requested_classes = set(request["params"]["classes"])
                action = "enable" if command == "enable_unsolicited" else "disable"
                if action == "enable":
                    unsolicited_classes.update(requested_classes)
                else:
                    unsolicited_classes.difference_update(requested_classes)
                write_json(
                    success(
                        request_id,
                        unsolicited_control_result(action, request["params"]),
                    )
                )
        elif command == "wait_unsolicited" and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            else:
                write_json(success(request_id, unsolicited_batch(unsolicited_classes)))
        elif command in {"integrity_poll", "class_poll", "read"} and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            else:
                write_json(success(request_id, read_result(request["params"])))
        elif command in {"select_and_operate", "direct_operate"} and args.mode == "tcp_api":
            if not connected:
                write_json(error(request_id, "NOT_CONNECTED"))
            elif request["params"].get("safety_token") != safety_token:
                write_json(error(request_id, "SAFETY_INTERLOCK"))
            elif request["params"].get("response_mode") == "no_response":
                write_json(error(request_id, "UNSUPPORTED_BY_BACKEND"))
            elif request["params"]["commands"][0].get("index") == 65534:
                time.sleep(60)
            elif request["params"]["commands"][0].get("index") == 65533:
                result = command_result(command, request["params"])
                result["execution_uncertain"] = True
                write_json(success(request_id, result))
            elif request["params"]["commands"][0].get("index") == 65532:
                result = command_result(command, request["params"])
                result.pop("summary")
                write_json(success(request_id, result))
            elif request["params"]["commands"][0].get("index") == 65531:
                result = command_result(command, request["params"])
                point = result["point_results"][0]
                point.update(
                    {
                        "state": "FAILURE",
                        "state_raw": 6,
                        "status": "TIMEOUT",
                        "status_raw": 1,
                        "status_backend": "TIMEOUT",
                    }
                )
                result["all_success"] = False
                result["summary"].update(
                    {"successful_points": 0, "failed_points": 1}
                )
                write_json(success(request_id, result))
            elif request["params"]["commands"][0].get("index") == 65530:
                result = command_result(command, request["params"])
                result["point_results"][0]["requested"]["index"] = 1
                write_json(success(request_id, result))
            elif request["params"]["commands"][0].get("index") in {65528, 65529}:
                result = command_result(command, request["params"])
                point = result["point_results"][0]
                raw = (
                    18
                    if request["params"]["commands"][0]["index"] == 65529
                    else 127
                )
                point.update(
                    {
                        "state": "FAILURE",
                        "state_raw": 6,
                        "status": "RESERVED" if raw == 18 else "UNDEFINED",
                        "status_raw": raw,
                        "status_backend": "BLOCKED" if raw == 18 else "UNDEFINED",
                        "status_reserved_2012": raw == 18,
                        "status_wire_raw_unambiguous": raw != 127,
                    }
                )
                result["all_success"] = False
                result["summary"].update(
                    {"successful_points": 0, "failed_points": 1}
                )
                write_json(success(request_id, result))
            elif request["params"]["commands"][0].get("index") == 65535:
                write_json(
                    error(
                        request_id,
                        "RESPONSE_TIMEOUT",
                        {
                            "execution_uncertain": True,
                            "may_still_execute": True,
                            "automatic_retry_safe": False,
                        },
                    )
                )
            else:
                write_json(success(request_id, command_result(command, request["params"])))
        else:
            write_json(error(request_id, "INVALID_REQUEST"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
