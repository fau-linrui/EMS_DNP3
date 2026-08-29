from __future__ import annotations

import json
from pathlib import Path

import pytest

from dnp3_master.pytest_plugin import (
    _load_capability_ids,
    _load_pics_capabilities,
)


def write_profile(path: Path, statuses: dict[str, str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "device": {
                    "vendor": "test",
                    "model": "test",
                    "firmware": "test",
                    "profile_revision": "test",
                },
                "capabilities": statuses,
            }
        ),
        encoding="utf-8",
    )
    return path


def configure_nested_test(pytester: pytest.Pytester, body: str) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(body)


def test_pics_loader_normalizes_valid_profile(tmp_path: Path) -> None:
    profile = write_profile(
        tmp_path / "profile.json",
        {"APP.FC.01.READ": "SUPPORTED", "APP.FC.03.SELECT": "NOT_SUPPORTED"},
    )

    assert _load_pics_capabilities(profile) == {
        "APP.FC.01.READ": "SUPPORTED",
        "APP.FC.03.SELECT": "NOT_SUPPORTED",
    }


def test_pics_loader_rejects_capability_absent_from_matrix(tmp_path: Path) -> None:
    profile = write_profile(
        tmp_path / "profile.json", {"APP.FC.99.TYPO": "SUPPORTED"}
    )

    with pytest.raises(pytest.UsageError, match="not present"):
        _load_pics_capabilities(
            profile, frozenset({"APP.FC.01.READ"})
        )


def test_capability_matrix_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    matrix.write_text(
        "capability_id,feature\nAPP.FC.01.READ,read\nAPP.FC.01.READ,again\n",
        encoding="utf-8",
    )

    with pytest.raises(pytest.UsageError, match="repeats"):
        _load_capability_ids(matrix)


def test_example_profile_uses_only_capabilities_from_matrix() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    capability_ids = _load_capability_ids(
        repository_root / "config" / "capability_matrix.csv"
    )

    capabilities = _load_pics_capabilities(
        repository_root / "config" / "ems_profile.example.json",
        capability_ids,
    )

    assert capabilities
    assert set(capabilities).issubset(capability_ids)


@pytest.mark.parametrize(
    "document, message",
    [
        ({"schema_version": 2, "capabilities": {}}, "schema_version"),
        ({"schema_version": 1, "capabilities": []}, "capabilities"),
        (
            {
                "schema_version": 1,
                "capabilities": {"APP.FC.01.READ": "MAYBE"},
            },
            "invalid status",
        ),
    ],
)
def test_pics_loader_rejects_invalid_profile(
    tmp_path: Path, document: object, message: str
) -> None:
    profile = tmp_path / "invalid.json"
    profile.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(pytest.UsageError, match=message):
        _load_pics_capabilities(profile)


def test_supported_dut_capability_runs(pytester: pytest.Pytester) -> None:
    profile = write_profile(
        pytester.path / "profile.json", {"APP.FC.01.READ": "SUPPORTED"}
    )
    configure_nested_test(
        pytester,
        """
        import pytest

        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.01.READ")
        def test_supported():
            assert True
        """,
    )

    result = pytester.runpytest("-q", "--dnp3-pics-file", str(profile))

    result.assert_outcomes(passed=1)


def test_unknown_dut_capability_does_not_run(pytester: pytest.Pytester) -> None:
    configure_nested_test(
        pytester,
        """
        import pytest

        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.01.READ")
        def test_unknown():
            raise AssertionError("UNKNOWN capability test must not execute")
        """,
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(xfailed=1)


def test_not_supported_selects_only_negative_behavior(
    pytester: pytest.Pytester,
) -> None:
    profile = write_profile(
        pytester.path / "profile.json", {"APP.FC.01.READ": "NOT_SUPPORTED"}
    )
    configure_nested_test(
        pytester,
        """
        import pytest

        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.01.READ")
        def test_positive_is_skipped():
            raise AssertionError("positive test must not execute")

        @pytest.mark.dnp3_unsupported_behavior
        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.01.READ")
        def test_negative_behavior_runs():
            assert True
        """,
    )

    result = pytester.runpytest("-q", "--dnp3-pics-file", str(profile))

    result.assert_outcomes(passed=1, skipped=1)


def test_state_changing_test_requires_explicit_authorization(
    pytester: pytest.Pytester,
) -> None:
    profile = write_profile(
        pytester.path / "profile.json", {"APP.FC.03.SELECT": "SUPPORTED"}
    )
    configure_nested_test(
        pytester,
        """
        import pytest

        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.03.SELECT")
        @pytest.mark.dnp3_state_changing
        def test_control():
            assert True
        """,
    )

    locked = pytester.runpytest("-q", "--dnp3-pics-file", str(profile))
    locked.assert_outcomes(skipped=1)

    authorized = pytester.runpytest(
        "-q",
        "--dnp3-pics-file",
        str(profile),
        "--dnp3-allow-state-changing",
        "--dnp3-operator-id",
        "test-operator",
        "--dnp3-dut-id",
        "test-dut",
    )
    authorized.assert_outcomes(passed=1)


def test_only_marked_dut_test_receives_safety_config(
    pytester: pytest.Pytester,
) -> None:
    profile = write_profile(
        pytester.path / "profile.json", {"APP.FC.03.SELECT": "SUPPORTED"}
    )
    configure_nested_test(
        pytester,
        """
        import pytest

        def test_unmarked_connection_stays_locked(dnp3_connection_config):
            assert dnp3_connection_config.safety is None

        @pytest.mark.dnp3_dut
        @pytest.mark.dnp3_capability("APP.FC.03.SELECT")
        @pytest.mark.dnp3_state_changing
        def test_marked_connection_is_unlocked(dnp3_connection_config):
            assert dnp3_connection_config.safety is not None
            assert dnp3_connection_config.safety.allow_state_change is True
        """,
    )

    result = pytester.runpytest(
        "-q",
        "--dnp3-pics-file",
        str(profile),
        "--dnp3-outstation-host",
        "127.0.0.1",
        "--dnp3-allow-state-changing",
        "--dnp3-operator-id",
        "test-operator",
        "--dnp3-dut-id",
        "test-dut",
    )

    result.assert_outcomes(passed=2)
