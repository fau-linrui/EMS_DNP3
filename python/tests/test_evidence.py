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


@pytest.mark.parametrize("collision", ["secret", "path", "truncation"])
def test_redacted_nodeid_collisions_keep_distinct_cases_and_phases(
    tmp_path: Path, collision: str,
) -> None:
    first_input = tmp_path / "private-a.json"
    second_input = tmp_path / "private-b.json"
    recorder = EvidenceRecorder(
        tmp_path / "evidence",
        inputs={"first": first_input, "second": second_input},
    )
    if collision == "secret":
        nodeids = ("test_write[dut_id=SIM-A]", "test_write[dut_id=SIM-B]")
    elif collision == "path":
        nodeids = (f"test_read[{first_input}]", f"test_read[{second_input}]")
    else:
        nodeids = tuple("x" * 3000 + middle + "y" * 3000 for middle in ("A", "B"))
    assert recorder._redact(nodeids[0], maximum_chars=4096) == recorder._redact(
        nodeids[1], maximum_chars=4096,
    )
    # Interleave phases so neither the redacted display name nor recording
    # adjacency can be used to recover a test's identity.
    for phase in ("setup", "call", "teardown"):
        for index, nodeid in enumerate(nodeids):
            recorder.record_phase(
                nodeid=nodeid, phase=phase,
                outcome="failed" if phase == "call" and index == 1 else "passed",
                duration_seconds=0.01,
                capabilities=(f"FAKE.CAP.{index}",),
            )
    manifest_path = recorder.finalize(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results_text = manifest_path.with_name("pytest-results.json").read_text(
        encoding="utf-8",
    )
    cases = json.loads(results_text)["tests"]
    assert manifest["results"]["test_count"] == 2
    assert manifest["results"]["phase_count"] == 6
    assert [case["case_id"] for case in cases] == ["case-000001", "case-000002"]
    assert cases[0]["nodeid"] == cases[1]["nodeid"]
    for index, case in enumerate(cases):
        assert [phase["phase"] for phase in case["phases"]] == [
            "setup", "call", "teardown",
        ]
        assert case["capabilities"] == [f"FAKE.CAP.{index}"]
        assert case["phases"][1]["outcome"] == ("passed" if index == 0 else "failed")
    for nodeid in nodeids:
        assert nodeid not in results_text
        assert hashlib.sha256(nodeid.encode("utf-8")).hexdigest() not in results_text
    assert recorder.finalize(1) == manifest_path


def test_pytest_evidence_keeps_redacted_parameterized_cases(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(test_sample='''
        import pytest

        @pytest.mark.parametrize("value", [1, 2], ids=["dut_id=SIM-A", "dut_id=SIM-B"])
        def test_parameter(value):
            assert value == 1
    ''')
    output = pytester.path / "evidence"
    result = pytester.runpytest("--dnp3-evidence-dir", str(output), "-q")
    result.assert_outcomes(passed=1, failed=1)
    run_directory, = output.iterdir()
    manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    document = json.loads((run_directory / "pytest-results.json").read_text(encoding="utf-8"))
    assert manifest["results"]["test_count"] == 2
    assert manifest["results"]["phase_count"] == 6
    assert len({case["case_id"] for case in document["tests"]}) == 2
    assert len({case["nodeid"] for case in document["tests"]}) == 1
