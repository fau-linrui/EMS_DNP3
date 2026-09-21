"""Offline trace decoding tests; no host, network, or device commands."""

from __future__ import annotations

import json
import struct

import pytest

from dnp3_master.trace import (
    TraceConfig, TraceDecoder, TraceIncompleteError, TraceRecord, TraceSummary,
)


def crc(data: bytes) -> bytes:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA6BC if value & 1 else 0)
    return (value ^ 0xFFFF).to_bytes(2, "little")


def frame(payload: bytes, control: int = 0xC4, source: int = 1, destination: int = 1024) -> bytes:
    header = bytes((5, 100, 5 + len(payload), control)) + destination.to_bytes(2, "little") + source.to_bytes(2, "little")
    return header + crc(header) + b"".join(
        payload[offset:offset + 16] + crc(payload[offset:offset + 16])
        for offset in range(0, len(payload), 16)
    )


def record(message: str, sequence: int = 1, level: str = "LINK_TX_HEX", session: int = 1) -> dict:
    return {"sequence": sequence, "session_id": session, "monotonic_ns": sequence * 10,
            "logger": "pytest-tcp-client", "level": level, "message": message,
            "message_truncated": False}


def records(raw: bytes, sequence: int = 1, direction: str = "TX", session: int = 1) -> list[dict]:
    chunks = [raw[:10]] + [raw[pos:pos + 18] for pos in range(10, len(raw), 18)]
    return [record(chunk.hex(" ").upper(), sequence + index, "LINK_" + direction + "_HEX", session)
            for index, chunk in enumerate(chunks)]


def batch(items: list[dict], *, last: int | None = None, state: str = "ACTIVE",
          queued: int = 0, dropped: int = 0, truncated: int = 0) -> dict:
    return {"trace_id": "trace-1", "state": state, "scope": "opendnp3_stack",
            "queue_capacity": 16384, "queued_records": queued,
            "dropped_records": dropped, "truncated_records": truncated,
            "last_sequence": last if last is not None else (items[-1]["sequence"] if items else 0),
            "complete": not (dropped or truncated), "records": items,
            "timed_out": not items}


def application(apdu: bytes, direction: str = "RX"):
    result = TraceDecoder().decode_batch(batch(records(frame(b"\xc0" + apdu), direction=direction), state="STOPPED"))
    assert len(result.applications) == 1
    return result, result.applications[0]


def test_config_defaults_and_boundaries():
    assert TraceConfig().to_params() == {"queue_capacity": 16384}
    assert TraceConfig(1).to_params()["queue_capacity"] == 1
    assert TraceConfig(65536).queue_capacity == 65536


@pytest.mark.parametrize("value", [0, -1, 65537, True, 2.0, "2", None])
def test_config_rejects_invalid_capacity(value):
    with pytest.raises(ValueError):
        TraceConfig(value)


@pytest.mark.parametrize("field,value", [
    ("queue_capacity", True), ("queued_records", 16385), ("last_sequence", -1),
    ("trace_id", "wrong"), ("trace_id", None), ("scope", "pcap"),
    ("state", "LOST"), ("complete", 1), ("dropped_records", 1),
])
def test_summary_is_strict(field, value):
    data = batch([])
    del data["records"], data["timed_out"]
    data[field] = value
    with pytest.raises(ValueError):
        TraceSummary.from_dict(data)


def test_summary_idle_and_unknown_fields():
    data = batch([])
    del data["records"], data["timed_out"]
    data.update(state="IDLE", trace_id=None)
    assert TraceSummary.from_dict(data).to_dict() == data
    data["extra"] = 1
    with pytest.raises(ValueError):
        TraceSummary.from_dict(data)


@pytest.mark.parametrize("field,value", [
    ("sequence", 0), ("session_id", True), ("monotonic_ns", -1),
    ("logger", "x" * 129), ("message", "x" * 1025),
    ("level", "RAW"), ("message_truncated", 0),
])
def test_records_are_strict(field, value):
    data = record("hello")
    data[field] = value
    with pytest.raises(ValueError):
        TraceRecord.from_dict(data)


def test_vendor_known_crc_vector_link_only():
    # Independent known vector from vendored cpp/tests/unit/TestCRC.cpp.
    raw = bytes.fromhex("05 64 05 C0 01 00 00 04 E9 21")
    decoded = TraceDecoder().decode_batch(batch(records(raw), state="STOPPED"))
    decoded.assert_complete()
    item = decoded.frames[0]
    assert item.raw_bytes == raw
    assert item.crc_valid and item.transport is None
    assert item.link["source"] == 1024 and item.link["destination"] == 1
    assert item.link["function_name"] == "RESET_LINK_STATES"
    assert decoded.applications == ()


def test_vendor_known_read_vector():
    raw = bytes.fromhex("05 64 14 F3 01 00 00 04 0A 3B C0 C3 01 3C 02 06 3C 03 06 3C 04 06 3C 01 06 9A 12")
    decoded = TraceDecoder().decode_batch(batch(records(raw), state="STOPPED"))
    decoded.assert_complete()
    app = decoded.applications[0]
    assert app.header["function_code"] == 1
    assert app.header["function_name"] == "READ"
    assert [obj["variation"] for obj in app.objects] == [2, 3, 4, 1]
    assert all(obj["header_only"] and not obj["values"] for obj in app.objects)


def test_cross_batch_single_record_reassembly_and_json_roundtrip():
    apdu = b"\xc0\x81\x00\x00\x1e\x05\x01\x00\x00\x07\x00" + b"\x01" + struct.pack("<f", 2.5)
    apdu += (b"\x01" + struct.pack("<f", 3.5)) * 7
    raw = frame(b"\xc0" + apdu)
    items = records(raw, direction="RX")
    decoder = TraceDecoder()
    for index, item in enumerate(items):
        result = decoder.decode_batch(batch([item], last=len(items), queued=len(items) - index - 1, state="STOPPED"))
        result.assert_complete()
        json.dumps(result.to_dict(), allow_nan=False)
        if index < len(items) - 1:
            assert not result.frames
        else:
            assert result.frames[0].raw_bytes == raw
            assert result.applications[0].raw_bytes == apdu
            assert len(result.applications[0].objects[0]["values"]) == 8


def test_multiframe_transport_wraparound_and_interleaved_direction():
    apdu = b"\xc0\x81\x00\x00\x01\x02\x00\x00\x01\x81\x01"
    first = records(frame(b"\x7f" + apdu[:5]), direction="RX")
    other = records(frame(b"\xc0\xc0\x00"), sequence=len(first) + 1)
    last = records(frame(b"\x80" + apdu[5:]), sequence=len(first) + len(other) + 1, direction="RX")
    decoder = TraceDecoder()
    result1 = decoder.decode_batch(batch(first + other))
    result1.assert_complete()
    assert len(result1.applications) == 1
    assert result1.applications[0].header["function_code"] == 0
    result2 = decoder.decode_batch(batch(last, state="STOPPED"))
    result2.assert_complete()
    assert result2.applications[0].raw_bytes == apdu
    assert result2.applications[0].frame_sequences == (1, len(first) + len(other) + 1)


@pytest.mark.parametrize("group,variation,value_bytes,expected", [
    (1, 2, b"\x81", True), (10, 2, b"\x01", False),
    (30, 5, b"\x01" + struct.pack("<f", 3.25), 3.25),
    (40, 3, b"\x01" + struct.pack("<f", -7.5), -7.5),
    (2, 2, b"\x81" + (123456789).to_bytes(6, "little"), True),
    (32, 7, b"\x01" + struct.pack("<f", 12.25) + (123456789).to_bytes(6, "little"), 12.25),
])
def test_ems_six_telemetry_layouts(group, variation, value_bytes, expected):
    result, app = application(b"\xf3\x82\x04\x00" + bytes((group, variation, 0x28, 1, 0, 7, 0)) + value_bytes)
    result.assert_complete()
    value = app.objects[0]["values"][0]
    assert value["index"] == 7 and value["value"] == expected
    assert value["flag_bits"]["ONLINE"] is True
    if group in (2, 32):
        assert value["time_ms"] == 123456789
    assert app.header["uns"] and app.header["con"]
    assert app.header["iin"]["bits"]["CLASS2_EVENTS"]


def test_read_index_request_headers_not_values():
    apdu = b"\xc0\x01\x01\x02\x17\x02\x07\x09\x1e\x05\x06"
    result, app = application(apdu, "TX")
    result.assert_complete()
    assert app.objects[0]["indices"] == [7, 9]
    assert not app.objects[0]["values"]
    assert app.objects[1]["group"] == 30


@pytest.mark.parametrize("function", [20, 21])
def test_unsolicited_class_request_headers(function):
    result, app = application(bytes((0xC0, function, 60, 2, 6, 60, 3, 6, 60, 4, 6)), "TX")
    result.assert_complete()
    assert len(app.objects) == 3


def test_restart_clear_is_passive_packed_write_decode():
    result, app = application(b"\xc0\x02\x50\x01\x00\x07\x07\x00", "TX")
    result.assert_complete()
    assert app.objects[0]["values"] == [{"index": 7, "value": False}]


@pytest.mark.parametrize("function", [3, 4, 5, 6, 129])
def test_crob_preserves_actual_wire_status(function):
    header = bytes((0xC0, function)) + (b"\x00\x00" if function == 129 else b"")
    body = b"\x0c\x01\x28\x01\x00\x09\x00" + bytes((3, 1)) + (100).to_bytes(4, "little") + bytes(4) + b"\x24"
    result, app = application(header + body)
    result.assert_complete()
    value = app.objects[0]["values"][0]
    assert value["operation"] == "LATCH_ON"
    assert value["count"] == 1 and value["on_time_ms"] == 100 and value["off_time_ms"] == 0
    assert value["status_wire_raw"] == 36 and value["status"] == "RESERVED"


@pytest.mark.parametrize("variation,fmt,value", [(1, "i", -123456), (2, "h", -123), (3, "f", 2.5), (4, "d", -900.25)])
def test_analog_commands_value_before_status(variation, fmt, value):
    result, app = application(bytes((0xC0, 5, 41, variation, 0x17, 1, 8)) + struct.pack("<" + fmt, value) + b"\x00", "TX")
    result.assert_complete()
    obj = app.objects[0]["values"][0]
    assert obj["value"] == value and obj["status_wire_raw"] == 0


@pytest.mark.parametrize("value,name", [(float("nan"), "NaN"), (float("inf"), "+Infinity"), (-float("inf"), "-Infinity")])
def test_nonfinite_wire_values_keep_raw_valid_json(value, name):
    result, app = application(b"\xc0\x81\x00\x00\x1e\x05\x00\x00\x00\x01" + struct.pack("<f", value))
    result.assert_complete()
    obj = app.objects[0]["values"][0]
    assert obj["value"] is None and obj["value_non_finite"] == name
    assert len(obj["raw_hex"]) == 10
    json.dumps(result.to_dict(), allow_nan=False)


def test_iin_2012_reserved_bits():
    result, app = application(b"\xc0\x81\x00\xc0")
    result.assert_complete()
    iin = app.header["iin"]
    assert iin["bits"]["RESERVED_2"] and iin["bits"]["RESERVED_1"]
    assert iin["raw_hex"] == "00C0"


def test_unknown_object_keeps_apdu_and_explicit_decode_failure_not_drop():
    apdu = b"\xc0\x81\x00\x00\xfa\x01\x17\x01\x00\xab\xcd"
    result, app = application(apdu)
    assert app.raw_bytes == apdu
    assert app.decode_error == "UNSUPPORTED_OBJECT:G250V1"
    assert result.summary.complete and not result.complete
    assert "UNSUPPORTED_OBJECT" in result.issues
    with pytest.raises(TraceIncompleteError) as caught:
        result.assert_complete()
    assert caught.value.batch is result


def test_invalid_crc_preserves_frame_without_fabricated_application():
    raw = bytearray(frame(b"\xc0\xc0\x01\x3c\x01\x06"))
    raw[-1] ^= 1
    result = TraceDecoder().decode_batch(batch(records(bytes(raw)), state="STOPPED"))
    assert result.frames[0].raw_bytes == bytes(raw)
    assert not result.frames[0].crc_valid
    assert result.frames[0].decode_error == "CRC_MISMATCH"
    assert not result.applications


def test_drop_gap_invalidates_partial_but_later_whole_frame_decodes():
    first = records(frame(b"\xc0\xc0\x01\x3c\x01\x06"))
    decoder = TraceDecoder()
    decoder.decode_batch(batch(first[:1], last=2, queued=1))
    complete = records(frame(b"\xc1\xc1\x00"), sequence=3)
    result = decoder.decode_batch(batch(complete, dropped=1, state="STOPPED"))
    assert len(result.frames) == 1
    assert result.applications[0].header["function_code"] == 0
    assert {"DROPPED_RECORDS", "RECORD_SEQUENCE_GAP"} <= set(result.issues)
    again = decoder.decode_batch(batch([], last=complete[-1]["sequence"], dropped=1, state="STOPPED"))
    assert not again.complete and again.issues == result.issues


def test_message_truncation_clears_partial():
    items = records(frame(b"\xc0\xc0\x01\x3c\x01\x06"))
    items[1]["message_truncated"] = True
    result = TraceDecoder().decode_batch(batch(items, truncated=1, state="STOPPED"))
    assert "TRUNCATED_RECORDS" in result.issues and not result.frames


def test_stop_drained_detects_half_link_and_transport_fragments():
    first = records(frame(b"\x40\xc0\x81"))
    decoder = TraceDecoder()
    active = decoder.decode_batch(batch(first))
    active.assert_complete()
    stopped = decoder.decode_batch(batch([], last=2, state="STOPPED"))
    assert "INCOMPLETE_TRANSPORT_FRAGMENT" in stopped.issues
    half = TraceDecoder().decode_batch(batch(first[:1], state="STOPPED"))
    assert "INCOMPLETE_LINK_FRAME" in half.issues


def test_session_routes_never_combine():
    first = records(frame(b"\x40\xc0\x81"), session=1)
    last = records(frame(b"\x81\x00\x00"), sequence=3, session=2)
    result = TraceDecoder().decode_batch(batch(first + last, state="STOPPED"))
    assert not result.applications
    assert "ORPHAN_TRANSPORT_SEGMENT" in result.issues
    assert "INCOMPLETE_TRANSPORT_FRAGMENT" in result.issues


def test_changed_trace_id_requires_explicit_reset():
    decoder = TraceDecoder()
    decoder.decode_batch(batch([]))
    data = batch([])
    data["trace_id"] = "trace-2"
    with pytest.raises(ValueError, match="trace_id changed"):
        decoder.decode_batch(data)
    decoder.reset()
    decoder.decode_batch(data).assert_complete()


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(extra=1),
    lambda b: b.update(records={}),
    lambda b: b.update(records=[record("")] * 1025),
    lambda b: b.update(records=[record("")], last_sequence=1, timed_out=True),
    lambda b: b.update(records=[record("")], last_sequence=0, timed_out=False),
])
def test_batch_contract_rejects_malformed_shape(mutate):
    data = batch([])
    mutate(data)
    with pytest.raises(ValueError):
        TraceDecoder().decode_batch(data)


def test_transport_sequence_gap_does_not_join_bytes():
    first = records(frame(b"\x40\xc0\x81"))
    last = records(frame(b"\x82\x00\x00"), sequence=3)
    result = TraceDecoder().decode_batch(batch(first + last, state="STOPPED"))
    assert "TRANSPORT_SEQUENCE_GAP" in result.issues
    assert not result.applications


@pytest.mark.parametrize("apdu,issue", [
    (b"\xc0", "TRUNCATED_APPLICATION_OBJECT"),
    (b"\xc0\x81\x00\x00\x1e\x05\x00\x00\x00\x01", "TRUNCATED_APPLICATION_OBJECT"),
    (b"\xc0\x81\x00\x00\x01\x02\x00\x02\x01", "INVALID_OBJECT_RANGE"),
    (b"\xc0\x81\x00\x00\x01\x02\x5b", "UNSUPPORTED_QUALIFIER"),
    (b"\xc0\x00\x01", "UNEXPECTED_CONFIRM_PAYLOAD"),
    (b"\xc0\x81\x00\x00\x01\x02\x06", "UNBOUNDED_OBJECT_VALUES"),
])
def test_malformed_application_is_raw_preserved(apdu, issue):
    result, app = application(apdu)
    assert issue in result.issues and app.raw_bytes == apdu


def test_transport_route_limit_is_explicit_and_bounded():
    items = []
    for source in range(17):
        items.extend(records(frame(b"\x40\xc0", source=source), sequence=len(items) + 1))
    result = TraceDecoder().decode_batch(batch(items))
    assert "TRANSPORT_ROUTE_LIMIT" in result.issues
    assert not result.applications


def test_transport_apdu_size_limit_is_explicit():
    decoder = TraceDecoder()
    sequence = 1
    result = None
    for index in range(264):
        header = (0x40 if index == 0 else 0) | (index & 63)
        items = records(frame(bytes((header,)) + b"\x00" * 249), sequence=sequence)
        sequence += len(items)
        result = decoder.decode_batch(batch(items))
    assert result is not None and "TRANSPORT_REASSEMBLY_LIMIT" in result.issues
    assert not result.applications


def test_maximum_292_byte_link_frame():
    # Maximum LPDU user data is 250 bytes: one transport octet and 249
    # application octets. The CRC block count is 16, not 15.
    apdu = b"\xc0\x81\x00\x00\x01\x02\x00\x00\xef" + b"\x81" * 240
    assert len(apdu) == 249
    raw = frame(b"\xc0" + apdu)
    assert len(raw) == 292
    result = TraceDecoder().decode_batch(batch(records(raw), state="STOPPED"))
    result.assert_complete()
    assert result.frames[0].raw_bytes == raw
    assert len(result.applications[0].objects[0]["values"]) == 240


def test_all_qualifier_widths_are_readable_without_emitting_requests():
    # Diagnostic decoding can preserve a captured 32-bit qualifier even though
    # the active OpenDNP3 read API does not emit 32-bit addresses.
    apdu = b"\xc0\x81\x00\x00\x01\x02\x39" + (1).to_bytes(4, "little") + (123456).to_bytes(4, "little") + b"\x81"
    result, app = application(apdu)
    result.assert_complete()
    assert app.objects[0]["qualifier_hex"] == "39"
    assert app.objects[0]["values"][0]["index"] == 123456


def test_application_fragments_keep_fir_fin_and_are_not_merged():
    first = records(frame(b"\xc0\xa1\x81\x00\x00"))
    second = records(frame(b"\xc1\x62\x81\x00\x00"), sequence=len(first) + 1)
    result = TraceDecoder().decode_batch(batch(first + second, state="STOPPED"))
    result.assert_complete()
    assert len(result.applications) == 2
    assert result.applications[0].header["fir"]
    assert not result.applications[0].header["fin"]
    assert not result.applications[1].header["fir"]
    assert result.applications[1].header["fin"]


def test_message_truncation_already_dropped_still_clears_partial():
    items = records(frame(b"\xc0\xc0\x00"))
    decoder = TraceDecoder()
    decoder.decode_batch(batch(items[:1], last=2, queued=1))
    result = decoder.decode_batch(batch([record("ordinary", 3, "INFO")], truncated=1, state="STOPPED"))
    assert "TRUNCATED_RECORDS" in result.issues
    assert not result.frames


def test_record_utf8_byte_bounds_and_direction():
    with pytest.raises(ValueError):
        TraceRecord.from_dict(record("\u6d4b" * 342))
    assert TraceRecord.from_dict(record("header", level="APP_HEADER_RX")).direction == "RX"
    assert TraceRecord.from_dict(record("header", level="APP_OBJECT_TX")).direction == "TX"
    assert TraceRecord.from_dict(record("header", level="INFO")).direction is None


@pytest.mark.parametrize("updates", [
    {"timed_out": False},
    {"queued_records": 1, "last_sequence": 1},
    {"last_sequence": 1, "queued_records": 1, "records": [record("foo", 1, "INFO")], "timed_out": False},
])
def test_inconsistent_empty_and_queued_batches_rejected(updates):
    data = batch([])
    data.update(updates)
    with pytest.raises(ValueError):
        TraceDecoder().decode_batch(data)


def test_stopped_summary_cannot_be_reactivated_or_decrease_counters():
    decoder = TraceDecoder()
    decoder.decode_batch(batch([record("foo", 1, "INFO")], state="STOPPED"))
    with pytest.raises(ValueError):
        decoder.decode_batch(batch([], last=1))
    with pytest.raises(ValueError):
        decoder.decode_batch(batch([], state="STOPPED"))


def test_link_route_limit_and_zero_length_transport_segment_limit():
    decoder = TraceDecoder()
    partials = []
    for session in range(1, 18):
        partials.extend(records(frame(b"\xc0\xc0\x00"), sequence=session, session=session)[:1])
    result = decoder.decode_batch(batch(partials))
    assert "LINK_ROUTE_LIMIT" in result.issues
    decoder.reset()
    sequence = 1
    for ordinal in range(1025):
        transport = (0x40 if ordinal == 0 else 0) | (ordinal & 63)
        items = records(frame(bytes((transport,))), sequence=sequence)
        sequence += len(items)
        result = decoder.decode_batch(batch(items))
    assert "TRANSPORT_REASSEMBLY_LIMIT" in result.issues


def test_unknown_qualifier_and_missing_object_indices_do_not_guess():
    apdu = b"\xc0\x01\x01\x02\x39" + (0xFFFFFFFF).to_bytes(4, "little")
    result, decoded = application(apdu)
    assert not result.complete
    assert decoded.raw_bytes == apdu
    assert "TRUNCATED_APPLICATION_OBJECT" in result.issues


def test_active_snapshot_missing_suffix_is_not_complete_and_clears_pending():
    decoder = TraceDecoder()
    first = records(frame(b"\xc0\xc0\x00"))[:1]
    result = decoder.decode_batch(batch(first, last=10))
    assert "RECORD_SEQUENCE_GAP" in result.issues and not result.complete
    result = decoder.decode_batch(batch([], last=10, state="STOPPED"))
    assert not result.complete
    assert "INCOMPLETE_LINK_FRAME" not in result.issues


def test_empty_active_snapshot_cannot_hide_already_consumed_records():
    result = TraceDecoder().decode_batch(batch([], last=10))
    assert "RECORD_SEQUENCE_GAP" in result.issues
    assert not result.complete


def test_unknown_function_without_payload_is_not_complete():
    result, app = application(b"\xc0\xff")
    assert app.header["function_code"] == 255
    assert app.header["function_name"] == "UNKNOWN"
    assert app.decode_error == "UNSUPPORTED_APPLICATION_FUNCTION:255"
    assert "UNSUPPORTED_APPLICATION_FUNCTION" in result.issues
    assert not result.complete


@pytest.mark.parametrize("control,payload,issue", [
    (0x20, b"", "INVALID_LINK_FCB"),
    (0xC2, b"", "INVALID_LINK_FCV"),
    (0xC3, b"\xc0\xc0\x00", "INVALID_LINK_FCV"),
    (0xD4, b"\xc0\xc0\x00", "INVALID_LINK_FCV"),
    (0xD0, b"", "INVALID_LINK_FCV"),
    (0xD9, b"", "INVALID_LINK_FCV"),
    (0x10, b"\xc0", "UNEXPECTED_LINK_PAYLOAD"),
    (0xC4, b"", "MISSING_TRANSPORT_HEADER"),
    (0xD3, b"", "MISSING_TRANSPORT_HEADER"),
])
def test_link_function_control_and_payload_constraints(control, payload, issue):
    raw = frame(payload, control=control)
    result = TraceDecoder().decode_batch(batch(records(raw), state="STOPPED"))
    assert result.frames[0].raw_bytes == raw and result.frames[0].crc_valid
    assert result.frames[0].decode_error == issue
    assert issue in result.issues and not result.complete


@pytest.mark.parametrize("control,payload", [
    (0x10, b""),  # secondary DFC is allowed
    (0xC0, b""), (0xE0, b""),  # primary FCB is ignored when FCV=0
    (0xE9, b""), (0xE4, b"\xc0\xc0\x00"),
    (0xD2, b""), (0xF2, b""),
    (0xD3, b"\xc0\xc0\x00"), (0xF3, b"\xc0\xc0\x00"),
])
def test_valid_link_fcv_dfc_combinations(control, payload):
    result = TraceDecoder().decode_batch(batch(records(frame(payload, control=control)), state="STOPPED"))
    result.assert_complete()
