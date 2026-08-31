from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import uuid

import pytest

from dnp3_master import PerformanceProfile


pytest_plugins = ("dnp3_master.pytest_plugin",)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("dnp3-performance-example")
    group.addoption(
        "--dnp3-performance-report-dir",
        action="store",
        default=None,
        help=(
            "Directory for immutable performance reports; may also be set with "
            "DNP3_PERFORMANCE_REPORT_DIR"
        ),
    )
    group.addoption(
        "--dnp3-run-soak",
        action="store_true",
        default=False,
        help="Explicitly run the profile's potentially 24-hour read-only soak",
    )


def _profile_parameter(config: pytest.Config) -> pytest.ParameterSet:
    profile: PerformanceProfile | None = getattr(
        config, "_dnp3_performance_profile", None
    )
    if profile is None:
        return pytest.param(
            None,
            marks=pytest.mark.skip(reason="no DNP3 performance profile configured"),
            id="no-performance-profile",
        )
    marks = [
        pytest.mark.dnp3_dut,
        pytest.mark.dnp3_performance,
        *(
            pytest.mark.dnp3_capability(identifier)
            for identifier in profile.required_capability_ids
        ),
    ]
    if profile.scope != "TARGET_ENVIRONMENT_PENDING_REVIEW":
        marks.append(
            pytest.mark.skip(
                reason=(
                    "real-DUT example requires scope "
                    "TARGET_ENVIRONMENT_PENDING_REVIEW"
                )
            )
        )
    return pytest.param(profile, marks=marks, id=profile.profile_id)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "performance_profile_case" in metafunc.fixturenames:
        metafunc.parametrize(
            "performance_profile_case",
            [_profile_parameter(metafunc.config)],
        )


@pytest.fixture(scope="session")
def performance_report_directory(pytestconfig: pytest.Config) -> Path:
    configured = pytestconfig.getoption(
        "--dnp3-performance-report-dir"
    ) or os.environ.get("DNP3_PERFORMANCE_REPORT_DIR")
    root = (
        Path(configured).expanduser().resolve(strict=False)
        if configured
        else (Path.cwd() / "evidence" / "local" / "performance").resolve()
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    run_id += uuid.uuid4().hex[:12]
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory
