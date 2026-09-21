"""Bounded, passive decoding of the host's opt-in OpenDNP3 stack trace.

This is not a packet sniffer. TX means encoded/queued, RX means accepted by the
stack's link parser. Rejected wire bytes and TCP retransmissions are not
available in this logging API. TX is not proof of socket write or delivery.
Never use a trace to infer command execution.

Object layouts are checked against the vendored OpenDNP3 3.1.2 ``gen/objects``
serializers. Unknown layouts retain the entire APDU and report an explicit
decode_error instead of guessing object boundaries. Applications are individual
application fragments (transport reassembled), not whole multi-fragment tasks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
import struct
from typing import Any, Mapping


_LEVELS = frozenset((
    "EVENT", "ERR", "WARN", "INFO", "DBG", "LINK_RX", "LINK_RX_HEX",
    "LINK_TX", "LINK_TX_HEX", "TRANSPORT_RX", "TRANSPORT_TX", "APP_HEADER_RX",
    "APP_HEADER_TX", "APP_OBJECT_RX", "APP_OBJECT_TX", "APP_HEX_RX", "APP_HEX_TX",
    "OTHER",
))
_SUMMARY_KEYS = frozenset((
    "trace_id", "state", "scope", "queue_capacity", "queued_records",
    "dropped_records", "truncated_records", "last_sequence", "complete",
))
_RECORD_KEYS = frozenset((
    "sequence", "session_id", "monotonic_ns", "logger", "level", "message",
    "message_truncated",
))
_IIN_NAMES = (
    "BROADCAST", "CLASS1_EVENTS", "CLASS2_EVENTS", "CLASS3_EVENTS", "NEED_TIME",
    "LOCAL_CONTROL", "DEVICE_TROUBLE", "DEVICE_RESTART", "NO_FUNC_CODE_SUPPORT",
    "OBJECT_UNKNOWN", "PARAMETER_ERROR", "EVENT_BUFFER_OVERFLOW",
    "ALREADY_EXECUTING", "CONFIG_CORRUPT", "RESERVED_2", "RESERVED_1",
)
_FUNCTIONS = {
    0: "CONFIRM", 1: "READ", 2: "WRITE", 3: "SELECT", 4: "OPERATE",
    5: "DIRECT_OPERATE", 6: "DIRECT_OPERATE_NR", 7: "IMMED_FREEZE",
    8: "IMMED_FREEZE_NR", 9: "FREEZE_CLEAR", 10: "FREEZE_CLEAR_NR",
    11: "FREEZE_AT_TIME", 12: "FREEZE_AT_TIME_NR", 13: "COLD_RESTART",
    14: "WARM_RESTART", 15: "INITIALIZE_DATA", 16: "INITIALIZE_APPLICATION",
    17: "START_APPLICATION", 18: "STOP_APPLICATION", 19: "SAVE_CONFIGURATION",
    20: "ENABLE_UNSOLICITED", 21: "DISABLE_UNSOLICITED", 22: "ASSIGN_CLASS",
    23: "DELAY_MEASURE", 24: "RECORD_CURRENT_TIME", 25: "OPEN_FILE",
    26: "CLOSE_FILE", 27: "DELETE_FILE", 28: "GET_FILE_INFO",
    29: "AUTHENTICATE_FILE", 30: "ABORT_FILE", 32: "AUTH_REQUEST",
    33: "AUTH_REQUEST_NO_ACK", 129: "RESPONSE", 130: "UNSOLICITED_RESPONSE",
    131: "AUTH_RESPONSE",
}
_LINK_FUNCTIONS = {
    0x40: "RESET_LINK_STATES", 0x42: "TEST_LINK_STATES",
    0x43: "CONFIRMED_USER_DATA", 0x44: "UNCONFIRMED_USER_DATA",
    0x49: "REQUEST_LINK_STATUS", 0: "ACK", 1: "NACK", 11: "LINK_STATUS",
    15: "NOT_SUPPORTED",
}
_STATUS_NAMES = {
    0: "SUCCESS", 1: "TIMEOUT", 2: "NO_SELECT", 3: "FORMAT_ERROR",
    4: "NOT_SUPPORTED", 5: "ALREADY_ACTIVE", 6: "HARDWARE_ERROR", 7: "LOCAL",
    8: "TOO_MANY_OBJS", 9: "NOT_AUTHORIZED", 10: "AUTOMATION_INHIBIT",
    11: "PROCESSING_LIMITED", 12: "OUT_OF_RANGE", 126: "NON_PARTICIPATING",
    127: "UNDEFINED",
}
_MAX_APDU = 65536
_MAX_ROUTES = 16
_MAX_SEGMENTS = 1024
_MAX_OBJECTS = 65536


def _integer(value: Any, name: str, minimum: int = 0, maximum: int = 2**64 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be boolean")
    return value


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} must be a string of at most {maximum} UTF-8 bytes")
    return value


def _fields(value: Any, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} has missing or unknown fields")
    return value


@dataclass(frozen=True, slots=True)
class TraceConfig:
    queue_capacity: int = 16384

    def __post_init__(self) -> None:
        _integer(self.queue_capacity, "queue_capacity", 1, 65536)

    def to_params(self) -> dict[str, Any]:
        return {"queue_capacity": self.queue_capacity}


@dataclass(frozen=True, slots=True)
class TraceSummary:
    trace_id: str | None
    state: str
    scope: str
    queue_capacity: int
    queued_records: int
    dropped_records: int
    truncated_records: int
    last_sequence: int
    complete: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceSummary:
        data = _fields(value, _SUMMARY_KEYS, "trace summary")
        state = data["state"]
        if state not in ("IDLE", "ACTIVE", "STOPPED"):
            raise ValueError("invalid trace state")
        trace_id = data["trace_id"]
        if state == "IDLE":
            if trace_id is not None:
                raise ValueError("IDLE trace_id must be null")
        elif not isinstance(trace_id, str) or re.fullmatch(r"trace-[1-9][0-9]{0,57}", trace_id) is None:
            raise ValueError("invalid trace_id")
        if data["scope"] != "opendnp3_stack":
            raise ValueError("unsupported trace scope")
        capacity = _integer(data["queue_capacity"], "queue_capacity", 1, 65536)
        queued = _integer(data["queued_records"], "queued_records", 0, capacity)
        dropped = _integer(data["dropped_records"], "dropped_records")
        truncated = _integer(data["truncated_records"], "truncated_records")
        last = _integer(data["last_sequence"], "last_sequence")
        complete = _boolean(data["complete"], "complete")
        if complete != (dropped == 0 and truncated == 0):
            raise ValueError("trace complete disagrees with loss counters")
        if queued > last or dropped > last or truncated > last:
            raise ValueError("trace counters exceed last_sequence")
        if state == "IDLE" and any((queued, dropped, truncated, last)):
            raise ValueError("IDLE trace counters must be zero")
        if state == "IDLE" and capacity != 16384:
            raise ValueError("IDLE trace capacity must be the default 16384")
        return cls(trace_id, state, data["scope"], capacity, queued, dropped, truncated, last, complete)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TraceRecord:
    sequence: int
    session_id: int
    monotonic_ns: int
    logger: str
    level: str
    message: str
    message_truncated: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceRecord:
        data = _fields(value, _RECORD_KEYS, "trace record")
        level = data["level"]
        if not isinstance(level, str) or level not in _LEVELS:
            raise ValueError("unknown trace log level")
        return cls(
            _integer(data["sequence"], "sequence", 1),
            _integer(data["session_id"], "session_id", 1),
            _integer(data["monotonic_ns"], "monotonic_ns"),
            _text(data["logger"], "logger", 128), level,
            _text(data["message"], "message", 1024),
            _boolean(data["message_truncated"], "message_truncated"),
        )

    @property
    def direction(self) -> str | None:
        if "_RX" in self.level:
            return "RX"
        if "_TX" in self.level:
            return "TX"
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TraceFrame:
    session_id: int
    direction: str
    first_sequence: int
    last_sequence: int
    monotonic_ns: int
    raw_hex: str
    payload_hex: str
    link: dict[str, Any]
    transport: dict[str, Any] | None
    crc_valid: bool
    decode_error: str | None = None

    @property
    def raw_bytes(self) -> bytes:
        return bytes.fromhex(self.raw_hex)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TraceApplication:
    session_id: int
    direction: str
    frame_sequences: tuple[int, ...]
    raw_hex: str
    header: dict[str, Any]
    objects: tuple[dict[str, Any], ...]
    decode_error: str | None = None

    @property
    def raw_bytes(self) -> bytes:
        return bytes.fromhex(self.raw_hex)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["frame_sequences"] = list(self.frame_sequences)
        result["objects"] = list(result["objects"])
        return result


class TraceIncompleteError(RuntimeError):
    """The trace lost data, or its full semantic decoding cannot be proved."""

    def __init__(self, batch: TraceBatch) -> None:
        self.batch = batch
        super().__init__("DNP3 trace is incomplete: " + "; ".join(batch.issues))


@dataclass(frozen=True, slots=True)
class TraceBatch:
    summary: TraceSummary
    records: tuple[TraceRecord, ...]
    frames: tuple[TraceFrame, ...]
    applications: tuple[TraceApplication, ...]
    timed_out: bool
    issues: tuple[str, ...]
    complete: bool

    def assert_complete(self) -> None:
        """Require both a lossless stack log and supported, consistent decoding.

        A pending transport/link fragment is normal until STOPPED and drained.
        This does not certify wire capture, task success, or device execution.
        """
        if not self.complete:
            raise TraceIncompleteError(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "records": [item.to_dict() for item in self.records],
            "frames": [item.to_dict() for item in self.frames],
            "applications": [item.to_dict() for item in self.applications],
            "timed_out": self.timed_out, "issues": list(self.issues),
            "complete": self.complete,
        }


def _crc(data: bytes) -> int:
    # Reflected DNP3 polynomial; initial zero, complemented result. Equivalent
    # to the table implementation in vendored src/link/CRC.cpp.
    value = 0
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xA6BC if value & 1 else 0)
    return (~value) & 0xFFFF


@dataclass(slots=True)
class _LinkPending:
    first: TraceRecord
    expected: int
    data: bytearray = field(default_factory=bytearray)


@dataclass(slots=True)
class _TransportPending:
    next_sequence: int
    data: bytearray = field(default_factory=bytearray)
    frames: list[int] = field(default_factory=list)


class TraceDecoder:
    """One stateful decoder per trace; call decode_batch in consumption order.

    Buffers at most 16 link and 16 transport routes, 65,536 APDU bytes and
    1,024 transport segments per route. Does not retain prior completed data.
    Sticky issue *categories* are bounded; raw detail remains in returned data.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._trace_id: str | None = None
        self._last_sequence = 0
        self._last_summary: TraceSummary | None = None
        self._issues: list[str] = []
        self._links: dict[tuple[int, str, str], _LinkPending] = {}
        self._transports: dict[tuple[int, str, int, int], _TransportPending] = {}

    def _issue(self, value: str) -> None:
        # Only fixed categories enter this list, never per-packet values/text.
        if value not in self._issues:
            self._issues.append(value)

    def _clear_pending(self) -> None:
        self._links.clear()
        self._transports.clear()

    def decode_batch(self, value: Mapping[str, Any]) -> TraceBatch:
        data = _fields(value, _SUMMARY_KEYS | {"records", "timed_out"}, "trace batch")
        summary = TraceSummary.from_dict({key: data[key] for key in _SUMMARY_KEYS})
        if summary.state == "IDLE":
            raise ValueError("cannot decode an IDLE trace")
        raw_records = data["records"]
        if not isinstance(raw_records, list) or len(raw_records) > 1024:
            raise ValueError("trace records must be an array of at most 1024 entries")
        records = tuple(TraceRecord.from_dict(item) for item in raw_records)
        timed_out = _boolean(data["timed_out"], "timed_out")
        if timed_out != (not records):
            raise ValueError("timed_out must equal absence of records")
        if (not records and summary.queued_records) or len(records) + summary.queued_records > summary.last_sequence:
            raise ValueError("trace batch record counts are inconsistent")
        if any(item.sequence > summary.last_sequence for item in records):
            raise ValueError("record sequence exceeds trace summary")
        if any(item.message_truncated for item in records) and not summary.truncated_records:
            raise ValueError("record truncation disagrees with summary")
        if self._trace_id is not None and self._trace_id != summary.trace_id:
            raise ValueError("trace_id changed; reset the decoder for a new trace")
        previous = self._last_summary
        if previous is not None:
            if summary.queue_capacity != previous.queue_capacity:
                raise ValueError("trace queue_capacity changed")
            for name in ("dropped_records", "truncated_records", "last_sequence"):
                if getattr(summary, name) < getattr(previous, name):
                    raise ValueError(f"trace {name} decreased")
            if previous.state == "STOPPED" and summary.state != "STOPPED":
                raise ValueError("stopped trace became active")
        self._trace_id = summary.trace_id
        if summary.dropped_records:
            self._issue("DROPPED_RECORDS")
            if previous is None or summary.dropped_records != previous.dropped_records:
                self._clear_pending()
        if summary.truncated_records:
            self._issue("TRUNCATED_RECORDS")
            if previous is None or summary.truncated_records != previous.truncated_records:
                self._clear_pending()
        frames: list[TraceFrame] = []
        applications: list[TraceApplication] = []
        for record in records:
            if record.sequence != self._last_sequence + 1:
                self._issue("RECORD_SEQUENCE_GAP")
                self._clear_pending()
            self._last_sequence = record.sequence
            if record.message_truncated:
                self._issue("TRUNCATED_RECORDS")
                self._clear_pending()
                continue
            if record.level not in ("LINK_RX_HEX", "LINK_TX_HEX"):
                continue
            frame = self._consume_link(record)
            if frame is not None:
                frames.append(frame)
                application = self._consume_transport(frame)
                if application is not None:
                    applications.append(application)
        # Native reads remove a prefix and take the counters under one lock.
        # Therefore even an ACTIVE read must reach the snapshot's consumed
        # boundary. A missing suffix cannot remain silently "complete" until
        # stop, nor be used as a continuation of buffered link/transport data.
        if self._last_sequence != summary.last_sequence - summary.queued_records:
            self._issue("RECORD_SEQUENCE_GAP")
            self._clear_pending()
        if summary.state == "STOPPED" and summary.queued_records == 0:
            if self._links:
                self._issue("INCOMPLETE_LINK_FRAME")
            if self._transports:
                self._issue("INCOMPLETE_TRANSPORT_FRAGMENT")
            self._clear_pending()
        self._last_summary = summary
        return TraceBatch(summary, records, tuple(frames), tuple(applications),
                          timed_out, tuple(self._issues), summary.complete and not self._issues)

    def _consume_link(self, record: TraceRecord) -> TraceFrame | None:
        direction = "RX" if record.level == "LINK_RX_HEX" else "TX"
        key = (record.session_id, direction, record.logger)
        text = record.message
        if re.fullmatch(r"[0-9A-Fa-f]{2}(?: [0-9A-Fa-f]{2}){0,17}", text) is None:
            self._issue("INVALID_LINK_HEX")
            self._links.pop(key, None)
            return None
        chunk = bytes.fromhex(text)
        pending = self._links.get(key)
        if pending is None:
            if len(chunk) != 10 or chunk[:2] != b"\x05\x64" or chunk[2] < 5:
                self._issue("ORPHAN_LINK_HEX")
                return None
            if len(self._links) >= _MAX_ROUTES:
                self._issue("LINK_ROUTE_LIMIT")
                return None
            payload_size = chunk[2] - 5
            expected = 10 + payload_size + ((payload_size + 15) // 16) * 2
            pending = _LinkPending(record, expected, bytearray(chunk))
            self._links[key] = pending
        else:
            remaining = pending.expected - len(pending.data)
            if len(chunk) != min(18, remaining):
                self._issue("INVALID_LINK_HEX_LENGTH")
                del self._links[key]
                return None
            pending.data.extend(chunk)
        if len(pending.data) != pending.expected:
            return None
        del self._links[key]
        raw = bytes(pending.data)
        valid = _crc(raw[:8]) == int.from_bytes(raw[8:10], "little")
        payload = bytearray()
        cursor = 10
        while cursor < len(raw):
            size = min(16, len(raw) - cursor - 2)
            block = raw[cursor:cursor + size]
            valid = valid and _crc(block) == int.from_bytes(raw[cursor + size:cursor + size + 2], "little")
            payload.extend(block)
            cursor += size + 2
        control = raw[3]
        function = control & 0x4F
        link = {
            "length": raw[2], "control_raw": control,
            "dir": bool(control & 0x80), "prm": bool(control & 0x40),
            "fcb": bool(control & 0x20), "fcv_dfc": bool(control & 0x10),
            "function_code": control & 0x0F,
            "function_name": _LINK_FUNCTIONS.get(function, "UNKNOWN"),
            "destination": int.from_bytes(raw[4:6], "little"),
            "source": int.from_bytes(raw[6:8], "little"),
            "header_crc_hex": raw[8:10].hex().upper(),
        }
        transport = None
        error = None
        if not valid:
            error = "CRC_MISMATCH"
        elif function not in _LINK_FUNCTIONS:
            error = "UNSUPPORTED_LINK_FUNCTION"
        elif control & 0x40 and bool(control & 0x10) != (function in (0x42, 0x43)):
            error = "INVALID_LINK_FCV"
        elif not control & 0x40 and control & 0x20:
            error = "INVALID_LINK_FCB"
        elif function in (0x43, 0x44):
            if not payload:
                error = "MISSING_TRANSPORT_HEADER"
            else:
                transport = {"raw": payload[0], "fir": bool(payload[0] & 0x40),
                             "fin": bool(payload[0] & 0x80), "sequence": payload[0] & 0x3F}
        elif payload:
            error = "UNEXPECTED_LINK_PAYLOAD"
        if error:
            self._issue(error)
        return TraceFrame(record.session_id, direction, pending.first.sequence,
                          record.sequence, pending.first.monotonic_ns,
                          raw.hex().upper(), bytes(payload).hex().upper(),
                          link, transport, valid, error)

    def _consume_transport(self, frame: TraceFrame) -> TraceApplication | None:
        key = (frame.session_id, frame.direction, frame.link["source"], frame.link["destination"])
        if frame.decode_error:
            self._transports.pop(key, None)
            return None
        header = frame.transport
        if header is None:
            return None
        payload = bytes.fromhex(frame.payload_hex)[1:]
        pending = self._transports.get(key)
        if header["fir"]:
            if pending is not None:
                self._issue("TRANSPORT_RESTART_BEFORE_FIN")
            if pending is None and len(self._transports) >= _MAX_ROUTES:
                self._issue("TRANSPORT_ROUTE_LIMIT")
                return None
            pending = _TransportPending(header["sequence"])
            self._transports[key] = pending
        elif pending is None:
            self._issue("ORPHAN_TRANSPORT_SEGMENT")
            return None
        assert pending is not None
        if pending.next_sequence != header["sequence"]:
            self._issue("TRANSPORT_SEQUENCE_GAP")
            del self._transports[key]
            return None
        if len(pending.data) + len(payload) > _MAX_APDU or len(pending.frames) >= _MAX_SEGMENTS:
            self._issue("TRANSPORT_REASSEMBLY_LIMIT")
            del self._transports[key]
            return None
        pending.next_sequence = (header["sequence"] + 1) & 0x3F
        pending.data.extend(payload)
        pending.frames.append(frame.first_sequence)
        if not header["fin"]:
            return None
        del self._transports[key]
        app = _decode_application(bytes(pending.data), frame.session_id, frame.direction, tuple(pending.frames))
        if app.decode_error:
            self._issue(app.decode_error.split(":", 1)[0])
        return app


class _DecodeError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        if size > self.remaining:
            raise _DecodeError("TRUNCATED_APPLICATION_OBJECT")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def integer(self, size: int) -> int:
        return int.from_bytes(self.take(size), "little")


def _decode_application(data: bytes, session_id: int, direction: str,
                        frames: tuple[int, ...]) -> TraceApplication:
    reader = _Reader(data)
    header: dict[str, Any] = {}
    objects: list[dict[str, Any]] = []
    error = None
    try:
        control, function = reader.take(2)
        header.update({"control_raw": control, "fir": bool(control & 0x80),
                       "fin": bool(control & 0x40), "con": bool(control & 0x20),
                       "uns": bool(control & 0x10), "sequence": control & 0x0F,
                       "function_code": function, "function_name": _FUNCTIONS.get(function, "UNKNOWN")})
        if function not in _FUNCTIONS:
            raise _DecodeError(f"UNSUPPORTED_APPLICATION_FUNCTION:{function}")
        if function in (129, 130, 131):
            iin = reader.take(2)
            numeric = int.from_bytes(iin, "little")
            header["iin"] = {"raw_hex": iin.hex().upper(), "iin1": iin[0], "iin2": iin[1],
                             "edition": "IEEE1815-2012",
                             "bits": {name: bool(numeric & (1 << index)) for index, name in enumerate(_IIN_NAMES)}}
        if function == 0 and reader.remaining:
            raise _DecodeError("UNEXPECTED_CONFIRM_PAYLOAD")
        header_only = function in (1, 7, 8, 9, 10, 20, 21, 22)
        if reader.remaining and not header_only and function not in (2, 3, 4, 5, 6, 129, 130):
            raise _DecodeError(f"UNSUPPORTED_APPLICATION_FUNCTION:{function}")
        total_objects = 0
        while reader.remaining:
            if len(objects) >= 4096:
                raise _DecodeError("OBJECT_HEADER_LIMIT")
            group, variation, qualifier = reader.take(3)
            obj: dict[str, Any] = {"group": group, "variation": variation,
                                   "qualifier": qualifier, "qualifier_hex": f"{qualifier:02X}",
                                   "values": []}
            objects.append(obj)
            prefix = (qualifier >> 4) & 7
            range_code = qualifier & 0x0F
            if qualifier & 0x80 or prefix not in (0, 1, 2, 3):
                raise _DecodeError(f"UNSUPPORTED_QUALIFIER:{qualifier:02X}")
            if range_code in (0, 1, 2):
                if prefix:
                    raise _DecodeError(f"UNSUPPORTED_QUALIFIER:{qualifier:02X}")
                width = 1 << range_code
                start, stop = reader.integer(width), reader.integer(width)
                if stop < start:
                    raise _DecodeError("INVALID_OBJECT_RANGE")
                count = stop - start + 1
                obj.update({"start": start, "stop": stop, "count": count})
            elif range_code == 6 and prefix == 0:
                count, start = None, 0
                obj["all_objects"] = True
            elif range_code in (7, 8, 9):
                count, start = reader.integer(1 << (range_code - 7)), 0
                obj["count"] = count
            else:
                raise _DecodeError(f"UNSUPPORTED_QUALIFIER:{qualifier:02X}")
            if header_only:
                obj["header_only"] = True
                if prefix:
                    if count is None or count > _MAX_OBJECTS or count * (1 << (prefix - 1)) > reader.remaining:
                        raise _DecodeError("TRUNCATED_APPLICATION_OBJECT")
                    obj["indices"] = [reader.integer(1 << (prefix - 1)) for _ in range(count)]
                continue
            if count is None:
                raise _DecodeError("UNBOUNDED_OBJECT_VALUES")
            total_objects += count
            if total_objects > _MAX_OBJECTS:
                raise _DecodeError("OBJECT_VALUE_LIMIT")
            if (group, variation) in ((1, 1), (3, 1), (10, 1), (80, 1)):
                if prefix:
                    raise _DecodeError("UNSUPPORTED_PACKED_INDEX_PREFIX")
                bits = 2 if group == 3 else 1
                packed = reader.take((count * bits + 7) // 8)
                for ordinal in range(count):
                    value = (packed[ordinal * bits // 8] >> (ordinal * bits % 8)) & ((1 << bits) - 1)
                    obj["values"].append({"index": start + ordinal, "value": bool(value) if bits == 1 else value})
                obj["raw_hex"] = packed.hex().upper()
                continue
            for ordinal in range(count):
                index = reader.integer(1 << (prefix - 1)) if prefix else (start + ordinal if range_code in (0, 1, 2) else None)
                offset = reader.offset
                item = _decode_value(reader, group, variation)
                item.update({"index": index, "raw_hex": data[offset:reader.offset].hex().upper()})
                obj["values"].append(item)
        header["decoded_bytes"] = reader.offset
    except _DecodeError as exc:
        error = str(exc)
        header["decoded_bytes"] = reader.offset
        header["remaining_hex"] = data[reader.offset:].hex().upper()
    return TraceApplication(session_id, direction, frames, data.hex().upper(), header, tuple(objects), error)


def _flags(raw: int, kind: str) -> dict[str, Any]:
    names = {0: "ONLINE", 1: "RESTART", 2: "COMM_LOST", 3: "REMOTE_FORCED", 4: "LOCAL_FORCED"}
    if kind == "binary":
        names.update({5: "CHATTER_FILTER", 6: "RESERVED", 7: "STATE"})
    elif kind == "binary_output":
        names.update({5: "RESERVED_5", 6: "RESERVED_6", 7: "STATE"})
    elif kind == "double_bit":
        names[5] = "CHATTER_FILTER"
    elif kind == "analog":
        names.update({5: "OVER_RANGE", 6: "REFERENCE_ERR", 7: "RESERVED"})
    elif kind == "counter":
        names.update({5: "ROLLOVER", 6: "DISCONTINUITY", 7: "RESERVED"})
    return {"flags": raw, "flag_bits": {name: bool(raw & (1 << bit)) for bit, name in names.items()}}


def _status(raw: int) -> dict[str, Any]:
    # This is the actual byte, unlike the public command result's enum-derived
    # status_raw. It must never be confused with the authorization/result API.
    return {"status_wire_raw": raw, "status": _STATUS_NAMES.get(raw, "RESERVED"),
            "status_edition": "IEEE1815-2012"}


def _number(reader: _Reader, fmt: str) -> dict[str, Any]:
    value = struct.unpack("<" + fmt, reader.take(struct.calcsize(fmt)))[0]
    if isinstance(value, float) and not math.isfinite(value):
        return {"value": None, "value_non_finite": "NaN" if math.isnan(value) else ("+Infinity" if value > 0 else "-Infinity")}
    return {"value": value}


def _decode_value(reader: _Reader, group: int, variation: int) -> dict[str, Any]:
    if (group, variation) in ((1, 2), (2, 1), (2, 2), (2, 3), (3, 2), (4, 1), (4, 2), (4, 3), (10, 2)):
        flags = reader.integer(1)
        kind = "double_bit" if group in (3, 4) else ("binary_output" if group == 10 else "binary")
        item = _flags(flags, kind)
        item["value"] = (flags >> 6) if group in (3, 4) else bool(flags & 0x80)
        if group in (2, 4) and variation == 2:
            item["time_ms"] = reader.integer(6)
        elif group in (2, 4) and variation == 3:
            item["relative_time_ms"] = reader.integer(2)
        return item
    if group == 12 and variation == 1:
        control = reader.integer(1)
        item = {"control_code": control, "operation_raw": control & 15,
                "operation": {0: "NULL", 1: "PULSE_ON", 2: "PULSE_OFF", 3: "LATCH_ON", 4: "LATCH_OFF"}.get(control & 15, "RESERVED"),
                "trip_close_raw": control >> 6,
                "trip_close": {0: "NULL", 1: "CLOSE", 2: "TRIP"}.get(control >> 6, "RESERVED"),
                "queue": bool(control & 16), "clear": bool(control & 32),
                "count": reader.integer(1), "on_time_ms": reader.integer(4),
                "off_time_ms": reader.integer(4)}
        item.update(_status(reader.integer(1)))
        return item
    if group == 30 and variation in range(1, 7):
        item = _flags(reader.integer(1), "analog") if variation not in (3, 4) else {}
        item.update(_number(reader, {1: "i", 2: "h", 3: "i", 4: "h", 5: "f", 6: "d"}[variation]))
        return item
    if group == 32 and variation in range(1, 9):
        item = _flags(reader.integer(1), "analog")
        item.update(_number(reader, {1: "i", 2: "h", 3: "i", 4: "h", 5: "f", 6: "d", 7: "f", 8: "d"}[variation]))
        if variation in (3, 4, 7, 8):
            item["time_ms"] = reader.integer(6)
        return item
    if group in (40, 41) and variation in range(1, 5):
        item = _flags(reader.integer(1), "analog") if group == 40 else {}
        item.update(_number(reader, {1: "i", 2: "h", 3: "f", 4: "d"}[variation]))
        if group == 41:
            item.update(_status(reader.integer(1)))
        return item
    if group in (20, 22) and variation in (1, 2, 5, 6):
        item = _flags(reader.integer(1), "counter") if group == 22 or variation in (1, 2) else {}
        item.update(_number(reader, "I" if variation in (1, 5) else "H"))
        if group == 22 and variation in (5, 6):
            item["time_ms"] = reader.integer(6)
        return item
    if group == 50 and variation in (1, 3):
        return {"time_ms": reader.integer(6)}
    raise _DecodeError(f"UNSUPPORTED_OBJECT:G{group}V{variation}")
