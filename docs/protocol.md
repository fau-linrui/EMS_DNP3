# DNP3 master host NDJSON protocol v1

`dnp3-master-host.exe` communicates exclusively through UTF-8 JSON Lines on stdin/stdout. T05 activates the fixed OpenDNP3 3.1.2 backend for TCP connection lifecycle only.

## Transport contract

- One request object per line; output contains exactly one response object per processed request.
- Requests normally end in LF; CRLF input is accepted. UTF-8 BOM and invalid UTF-8 are rejected.
- stdout contains protocol JSON only. Fatal startup or stream errors use stderr and a nonzero process exit code.
- The default request limit is 1 MiB and can be lowered or raised (64 bytes through 16 MiB) with `--max-request-bytes`.
- JSON nesting is limited to 64 levels. Duplicate object keys, unknown envelope fields and non-object `params` are rejected.
- A process remembers the latest 4,096 request IDs; reuse inside that window returns `INVALID_REQUEST` with reason `duplicate_id`.

## Request envelope

```json
{"schema_version":1,"id":"req-1","cmd":"hello","params":{}}
```

All four fields are required and no other top-level fields are permitted. `id` is a 1–128 byte ASCII token. `cmd` is a 1–64 byte ASCII token. The machine-readable definitions are `schemas/request.schema.json` and `schemas/response.schema.json`.

## Response envelopes

Success:

```json
{"schema_version":1,"id":"req-1","ok":true,"result":{}}
```

Failure:

```json
{"schema_version":1,"id":"req-1","ok":false,"error":{"code":"INVALID_REQUEST","message":"request command is unknown","details":{"reason":"unknown_command"}}}
```

Clients must branch on `error.code` and structured `details`, never on the human-readable `message`.

## Implemented commands

| Command | T05 behavior |
|---|---|
| `hello` | Returns host/build identity, `opendnp3`/`3.1.2`, limits, capability revisions and only implemented commands. |
| `get_status` | Returns host state, channel/session/event-queue state and request counters. |
| `connect` | Creates Manager → TCP client Channel → Master, enables it and waits for `OPEN`. |
| `disconnect` | Disables/shuts down Master, shuts down Channel, then shuts down Manager. |
| `wait_event` | Waits for and consumes a bounded batch of queued channel-state events. |
| `shutdown` | Cleans any active backend resources, returns `SHUTTING_DOWN`, flushes and exits normally. |

`hello`, `get_status`, `disconnect` and `shutdown` require empty `params`. Read, poll and operate commands remain `UNSUPPORTED_BY_BACKEND`; unknown commands return `INVALID_REQUEST`. No measurement or command result is fabricated.

### `connect` parameters

```json
{
  "host": "192.0.2.10",
  "port": 20000,
  "local_adapter": "0.0.0.0",
  "connect_timeout_ms": 5000,
  "retry": {"min_ms": 1000, "max_ms": 60000},
  "link": {
    "master_address": 1,
    "outstation_address": 1024,
    "keep_alive_timeout_ms": 60000
  }
}
```

Only `host` is required. Port defaults to the registered DNP3 port 20000. Ports are 1–65535; connect timeout is 50–300000 ms; retry delays are 10–300000 ms with `min_ms <= max_ms`; keep-alive is 1000–86400000 ms and cannot be disabled. Individual link addresses are 0–65519, must differ, and exclude IEEE 1815 special/reserved addresses 0xFFF0–0xFFFF. Unknown or mistyped fields are rejected.

### `wait_event` parameters and result

`timeout_ms` defaults to 0 and is limited to 60000; `max_events` defaults to 64 and is limited to 1–256. The result contains `events`, `timed_out`, `remaining` and `dropped_total`. Each event has a monotonic `sequence`, `session_id`, `type: "channel_state"`, `state` (`CLOSED`, `OPENING`, `OPEN`, or `SHUTDOWN`) and `monotonic_ns`. The queue holds at most 1024 entries and drops the oldest entry on overflow while incrementing `dropped_total`.

## Error behavior implemented through T05

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | Invalid syntax/envelope, duplicate ID, unexpected parameters, or unknown command. |
| `REQUEST_TOO_LARGE` | Input line exceeded the configured byte limit; reading resumes at the next line. |
| `SCHEMA_MISMATCH` | `schema_version` is not 1. |
| `NOT_CONNECTED` | `disconnect` was requested without an active session. |
| `ALREADY_CONNECTED` | `connect` was requested while a session was active. |
| `CONNECTION_TIMEOUT` | The channel did not reach `OPEN` before its deadline; resources were cleaned. |
| `UNSUPPORTED_BY_BACKEND` | Recognized command is not implemented in the current task scope. |
| `PROCESS_SHUTTING_DOWN` | A request reached the controller after shutdown began. |
| `INTERNAL_ERROR` | An unexpected processing exception was contained. |

The response schema reserves the complete minimum error-code vocabulary from the development guide for later task cards.
