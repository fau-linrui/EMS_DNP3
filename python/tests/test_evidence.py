from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dnp3_master.evidence import EvidenceRecorder, redact_text


def test_redaction_covers_safety_and_operator_secrets() -> None:
    token = "0123456789abcdef0123456789abcdef"
    redacted = redact_text(
        '{"safety_token":"'
        + token
        + '","operator_id":"alice","dut_id":"secret-dut"} '
        "password=hunter2 Authorization: Bearer abc.def"
    )

    assert token not in redacted
    assert "alice" not in redacted
    assert "secret-dut" not in redacted
    assert "hunter2" not in redacted
    assert "abc.def" not in redacted
    assert redacted.count("<redacted>") >= 4


def test_evidence_records_hashes_not_private_input_contents(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    private = tmp_path / "points.local.csv"
    private.write_text("private-point-name,private-address\n", encoding="utf-8")
    host_dir = tmp_path / "bin"
    host_dir.mkdir()
    host = host_dir / "dnp3-master-host.exe"
    host.write_bytes(b"test-host")
    (host_dir / "build-info.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "host_version": "test",
                "git_commit": "0" * 40,
                "unexpected_private_field": "must-not-be-copied",
            }
        ),
        encoding="utf-8",
    )
    recorder = EvidenceRecorder(
        output,
        execution_root=tmp_path,
        host_executable=host,
        inputs={"point_table": private},
        runner={"pytest_version": "test"},
    )
    token = "0123456789abcdef0123456789abcdef"
    recorder.record_phase(
        nodeid="test_demo.py::test_point",
        phase="call",
        outcome="failed",
        duration_seconds=0.25,
        markers=("dnp3_dut",),
        capabilities=("APP.FC.01.READ",),
        failure=f'{{"safety_token":"{token}"}} at {tmp_path / "private.py"}',
        captured_output=(
            f"operator_id=alice dut_id=private-dut input={private.resolve()}"
        ),
    )
    manifest_path = recorder.finalize(1)

    manifest_text = manifest_path.read_text(encoding="utf-8")
    results_path = manifest_path.with_name("pytest-results.json")
    results_text = results_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["state"] == "COMPLETED"
    assert manifest["pytest_exit_status"] == 1
    assert manifest["inputs"]["point_table"] == {
        "present": True,
        "file_name": "points.local.csv",
        "size_bytes": private.stat().st_size,
        "sha256": hashlib.sha256(private.read_bytes()).hexdigest(),
    }
    assert manifest["build_info"]["host_version"] == "test"
    assert "unexpected_private_field" not in manifest["build_info"]
    assert manifest["results"]["sha256"] == hashlib.sha256(
        results_path.read_bytes()
    ).hexdigest()
    combined = manifest_text + results_text
    for secret in (
        "private-point-name",
        "private-address",
        token,
        "alice",
        "private-dut",
        "must-not-be-copied",
        str(tmp_path),
        tmp_path.as_posix(),
    ):
        assert secret not in combined
    assert manifest["redaction"] == {
        "absolute_input_paths_in_metadata_stored": False,
        "arbitrary_test_output_requires_review": True,
        "known_absolute_paths_redacted": True,
        "private_input_contents_stored": False,
        "report_text_limit_chars": 16_384,
        "sensitive_fields_redacted": True,
    }


def test_pytest_plugin_creates_opt_in_evidence(pytester: pytest.Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(
        test_sample="""
        def test_evidence_sample():
            print("operator_id=private-user")
            assert True
        """
    )
    output = pytester.path / "evidence"

    result = pytester.runpytest("--dnp3-evidence-dir", str(output), "-q")

    result.assert_outcomes(passed=1)
    run_directories = [path for path in output.iterdir() if path.is_dir()]
    assert len(run_directories) == 1
    manifest = json.loads(
        (run_directories[0] / "manifest.json").read_text(encoding="utf-8")
    )
    results = (run_directories[0] / "pytest-results.json").read_text(
        encoding="utf-8"
    )
    assert manifest["results"]["test_count"] == 1
    assert manifest["results"]["phase_count"] == 3
    assert "private-user" not in results
