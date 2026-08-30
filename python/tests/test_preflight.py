from __future__ import annotations

import json
from pathlib import Path

import pytest

from dnp3_master.ems_profile import EmsProfileError, load_ems_profile
from dnp3_master.preflight import build_preflight_report, main


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPOSITORY_ROOT / "config"


def _json_document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_ready_profile(path: Path) -> Path:
    document = _json_document(CONFIG / "ems_profile.example.json")
    document["device"] = {
        "vendor": "Laboratory Vendor",
        "model": "EMS DNP3 Test Endpoint",
        "firmware": "2026.08",
        "profile_revision": "approved-17",
    }
    capabilities = document["capabilities"]
    assert isinstance(capabilities, dict)
    document["capabilities"] = {
        capability_id: "SUPPORTED" for capability_id in capabilities
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _write_all_scenarios_plan(path: Path) -> Path:
    document = _json_document(CONFIG / "ems_test_plan.example.json")
    for scenario in document["unsolicited_scenarios"]:
        scenario["enabled"] = True
    for scenario in document["control_scenarios"]:
        scenario["enabled"] = True
        scenario["authorization_reference"] = "LAB-TICKET-2026-0017"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_profile_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    profile = tmp_path / "duplicate.json"
    profile.write_text(
        """
        {
          "schema_version": 1,
          "device": {
            "vendor": "test",
            "model": "test",
            "firmware": "test",
            "profile_revision": "test"
          },
          "capabilities": {
            "APP.FC.01.READ": "SUPPORTED",
            "APP.FC.01.READ": "UNKNOWN"
          }
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(EmsProfileError, match="duplicate JSON key"):
        load_ems_profile(profile)


def test_profile_loader_rejects_whitespace_identity_and_unbounded_ids(
    tmp_path: Path,
) -> None:
    document = {
        "schema_version": 1,
        "device": {
            "vendor": "   ",
            "model": "test",
            "firmware": "test",
            "profile_revision": "test",
        },
        "capabilities": {"A" * 257: "SUPPORTED"},
    }
    profile = tmp_path / "invalid-bounds.json"
    profile.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(EmsProfileError, match="device.vendor"):
        load_ems_profile(profile)

    document["device"]["vendor"] = "test"
    profile.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(EmsProfileError, match="capability IDs"):
        load_ems_profile(profile)


def test_example_configuration_is_valid_but_fail_closed() -> None:
    report = build_preflight_report(
        pics_path=CONFIG / "ems_profile.example.json",
        points_path=CONFIG / "points.example.csv",
        plan_path=CONFIG / "ems_test_plan.example.json",
        capability_matrix_path=CONFIG / "capability_matrix.csv",
    )

    assert report["ready"] is False
    assert report["scope"] == "OFFLINE_CONFIGURATION_ONLY"
    blocker_codes = {item["code"] for item in report["blockers"]}
    assert "DEVICE_IDENTITY_PLACEHOLDER" in blocker_codes
    assert "DUT_CAPABILITY_NOT_SUPPORTED" in blocker_codes
    assert report["summary"]["enabled_points"] == 4
    assert report["summary"]["enabled_poll_scenarios"] == 4


def test_complete_private_configuration_reports_ready(tmp_path: Path) -> None:
    profile = _write_ready_profile(tmp_path / "profile.json")
    plan = _write_all_scenarios_plan(tmp_path / "plan.json")

    report = build_preflight_report(
        pics_path=profile,
        points_path=CONFIG / "points.example.csv",
        plan_path=plan,
        capability_matrix_path=CONFIG / "capability_matrix.csv",
    )

    assert report["ready"] is True
    assert report["blockers"] == []
    assert report["warnings"] == []
    capability_ids = {
        item["capability_id"] for item in report["required_capabilities"]
    }
    assert {
        "CHANNEL.TCP.CLIENT",
        "APP.FC.01.READ",
        "APP.FC.03.SELECT",
        "APP.FC.04.OPERATE",
        "APP.FC.05.DIRECT_OPERATE",
        "APP.UNSOLICITED",
        "APP.FC.82.UNSOLICITED_RESPONSE",
        "OBJ.G2.V2",
        "OBJ.G12.V1",
        "OBJ.G32.V7",
        "OBJ.G41.V3",
    }.issubset(capability_ids)
    assert all(item["ready"] for item in report["required_capabilities"])


def test_cli_json_exit_codes_distinguish_not_ready_and_ready(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = [
        "--points",
        str(CONFIG / "points.example.csv"),
        "--plan",
        str(CONFIG / "ems_test_plan.example.json"),
        "--capability-matrix",
        str(CONFIG / "capability_matrix.csv"),
        "--json",
    ]
    assert main(
        ["--pics", str(CONFIG / "ems_profile.example.json"), *common]
    ) == 3
    not_ready = json.loads(capsys.readouterr().out)
    assert not_ready["ready"] is False

    profile = _write_ready_profile(tmp_path / "profile.json")
    assert main(["--pics", str(profile), *common]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["ready"] is True


def test_cli_invalid_configuration_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")

    result = main(
        [
            "--pics",
            str(invalid),
            "--points",
            str(CONFIG / "points.example.csv"),
            "--plan",
            str(CONFIG / "ems_test_plan.example.json"),
            "--capability-matrix",
            str(CONFIG / "capability_matrix.csv"),
            "--json",
        ]
    )

    assert result == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "INVALID_CONFIGURATION"
