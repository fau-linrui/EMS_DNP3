from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from dnp3_master import (
    SafetyIncidentAcknowledgmentError,
    SafetyIncidentPersistenceError,
    SafetyIncidentStore,
    UnresolvedSafetyIncidentError,
    dut_identity_sha256,
)


def record_incident(store: SafetyIncidentStore, dut_id: str = "PRIVATE-DUT-007"):
    return store.record_uncertain(
        dut_id=dut_id,
        operation="direct_operate",
        command_payload={
            "operation": "direct_operate",
            "commands": [
                {
                    "type": "analog_output_float32",
                    "index": 12,
                    "value": 98765.4321,
                }
            ],
        },
        points=[{"type": "analog_output_float32", "index": 12}],
        request_id="py-test-request-1",
        error_code="RESPONSE_TIMEOUT",
        execution_uncertain=True,
        may_still_execute=True,
    )


def test_incident_lock_uses_dut_hash_and_omits_command_values(tmp_path: Path) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    recorded = record_incident(store)

    assert recorded.created is True
    incident = recorded.incident
    assert incident["status"] == "OPEN"
    assert incident["dut_identity_sha256"] == dut_identity_sha256("PRIVATE-DUT-007")
    assert incident["command"]["points"] == [
        {"type": "analog_output_float32", "index": 12}
    ]
    active_path = (
        store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    )
    persisted = active_path.read_text(encoding="utf-8")
    assert "PRIVATE-DUT-007" not in persisted
    assert "98765.4321" not in persisted
    assert '"value"' not in persisted

    coalesced = record_incident(store)
    assert coalesced.created is False
    assert coalesced.incident["incident_id"] == incident["incident_id"]
    with pytest.raises(UnresolvedSafetyIncidentError) as blocked:
        store.assert_clear("PRIVATE-DUT-007")
    assert blocked.value.incident_id == incident["incident_id"]


def test_acknowledgment_requires_matching_incident_and_hashes_readback(
    tmp_path: Path,
) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    incident_id = str(record_incident(store).incident["incident_id"])
    readback = {
        "source": "independent-static-read",
        "observed_private_value": "SECRET-READBACK-VALUE",
    }

    with pytest.raises(SafetyIncidentAcknowledgmentError, match="does not match"):
        store.acknowledge(
            dut_id="PRIVATE-DUT-007",
            incident_id="INC-00000000-0000-0000-0000-000000000000",
            acknowledged_by="reviewer-1",
            readback_summary="Independent read completed",
            readback=readback,
            evidence_reference="evidence/run-001",
        )
    with pytest.raises(SafetyIncidentAcknowledgmentError, match="non-empty object"):
        store.acknowledge(
            dut_id="PRIVATE-DUT-007",
            incident_id=incident_id,
            acknowledged_by="reviewer-1",
            readback_summary="Independent read completed",
            readback={},
            evidence_reference="evidence/run-001",
        )

    archived = store.acknowledge(
        dut_id="PRIVATE-DUT-007",
        incident_id=incident_id,
        acknowledged_by="reviewer-1",
        readback_summary="Independent read completed",
        readback=readback,
        evidence_reference="evidence/run-001",
    )
    assert archived["status"] == "ACKNOWLEDGED"
    assert len(archived["acknowledgment"]["readback_payload_sha256"]) == 64
    assert store.get_active("PRIVATE-DUT-007") is None
    store.assert_clear("PRIVATE-DUT-007")
    persisted = (store.archive_directory / f"{incident_id}.json").read_text(
        encoding="utf-8"
    )
    assert "SECRET-READBACK-VALUE" not in persisted
    assert "PRIVATE-DUT-007" not in persisted


def test_corrupt_active_record_fails_closed(tmp_path: Path) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    store.active_directory.mkdir(parents=True)
    active_path = (
        store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    )
    active_path.write_text('{"schema_version":1', encoding="utf-8")

    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.get_active("PRIVATE-DUT-007")
    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.assert_clear("PRIVATE-DUT-007")


def test_acknowledgment_retry_recovers_after_archive_before_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    incident_id = str(record_incident(store).incident["incident_id"])
    active_path = (
        store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    )
    original_unlink = Path.unlink
    failed_once = False

    def fail_active_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failed_once
        if path == active_path and not failed_once:
            failed_once = True
            raise OSError("simulated interruption")
        original_unlink(path, *args, **kwargs)

    acknowledgment = {
        "dut_id": "PRIVATE-DUT-007",
        "incident_id": incident_id,
        "acknowledged_by": "reviewer-1",
        "readback_summary": "Independent read completed",
        "readback": {"group": 40, "variation": 3, "index": 12, "value": 1.0},
        "evidence_reference": "evidence/run-002",
    }
    monkeypatch.setattr(Path, "unlink", fail_active_unlink)
    with pytest.raises(SafetyIncidentPersistenceError, match="active lock remains"):
        store.acknowledge(**acknowledgment)
    assert active_path.is_file()
    assert (store.archive_directory / f"{incident_id}.json").is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    result = store.acknowledge(**acknowledgment)
    assert result["status"] == "ACKNOWLEDGED"
    assert not active_path.exists()


def test_non_directory_store_path_fails_without_creating_a_lock(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")
    store = SafetyIncidentStore(occupied)

    with pytest.raises(SafetyIncidentPersistenceError, match="cannot create"):
        record_incident(store)


def test_incident_files_are_valid_json_without_duplicate_keys(tmp_path: Path) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    incident = record_incident(store).incident
    path = store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == incident


def test_incident_cli_status_has_clean_machine_readable_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "dnp3_master.safety_incident_cli",
            "--directory",
            str(tmp_path / "incidents"),
            "status",
            "--dut-id",
            "PRIVATE-DUT-007",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5.0,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {"active": None}


def test_active_lock_does_not_use_suppressed_stat_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    incident = record_incident(store).incident
    active_path = store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    original_stat = os.stat

    def denied_metadata(path, *args, **kwargs):
        if Path(path) == active_path:
            raise PermissionError("simulated metadata access denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", denied_metadata)
    # On Python 3.14 Path.exists would swallow this PermissionError. Direct
    # bounded reading still sees the active lock and blocks dispatch.
    assert store.get_active("PRIVATE-DUT-007") == incident
    with pytest.raises(UnresolvedSafetyIncidentError):
        store.assert_clear("PRIVATE-DUT-007")


@pytest.mark.parametrize("failure", [PermissionError, OSError, IsADirectoryError])
def test_unreadable_active_lock_remains_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[OSError]
) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    incident = record_incident(store).incident
    active_path = store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    original_open = Path.open

    def denied_read(path, *args, **kwargs):
        if path == active_path:
            raise failure("simulated unreadable incident")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_read)
    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.assert_clear("PRIVATE-DUT-007")
    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.acknowledge(
            dut_id="PRIVATE-DUT-007", incident_id=incident["incident_id"],
            acknowledged_by="test-reviewer", readback_summary="test readback",
            readback={"value": 1}, evidence_reference="test-only/evidence",
        )
    # The failed operation never deletes or modifies an existing record.
    monkeypatch.setattr(Path, "open", original_open)
    assert store.get_active("PRIVATE-DUT-007") == incident


def test_missing_open_with_existing_directory_entry_is_not_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SafetyIncidentStore(tmp_path / "incidents")
    record_incident(store)
    active_path = store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    original_open = Path.open

    def missing_target(path, *args, **kwargs):
        if path == active_path:
            # Models a dangling symlink; no filesystem symlink permission is
            # required to exercise the ownership check on Windows.
            raise FileNotFoundError("simulated missing link target")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", missing_target)
    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.assert_clear("PRIVATE-DUT-007")


def test_incident_read_is_bounded_even_if_file_grows_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO
    from dnp3_master.safety_incidents import _MAX_INCIDENT_BYTES

    store = SafetyIncidentStore(tmp_path / "incidents")
    store._ensure_directories()
    active_path = store.active_directory / f"{dut_identity_sha256('PRIVATE-DUT-007')}.json"
    original_open = Path.open
    sizes = []

    class GrowingFile(BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    def grown_record(path, *args, **kwargs):
        if path == active_path:
            return GrowingFile(b"x" * (_MAX_INCIDENT_BYTES * 2))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", grown_record)
    with pytest.raises(UnresolvedSafetyIncidentError, match="remains locked"):
        store.assert_clear("PRIVATE-DUT-007")
    assert sizes == [_MAX_INCIDENT_BYTES + 1]
