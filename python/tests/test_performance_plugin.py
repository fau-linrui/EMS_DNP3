from __future__ import annotations

from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PERFORMANCE = REPOSITORY_ROOT / "config" / "performance_profile.example.json"
EVENTS = REPOSITORY_ROOT / "config" / "local_event_profile.example.json"


def test_plugin_loads_hashed_performance_and_local_event_profiles(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeconftest('pytest_plugins = ("dnp3_master.pytest_plugin",)')
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.dnp3_performance
        @pytest.mark.dnp3_soak
        def test_profiles(dnp3_performance_profile, dnp3_local_event_profile):
            assert dnp3_performance_profile.source_sha256
            assert dnp3_performance_profile.soak.target_duration_seconds == 86400
            assert dnp3_local_event_profile.source_sha256
            assert len(dnp3_local_event_profile.loads) == 2
        """
    )
    result = pytester.runpytest(
        "-q",
        "-p",
        "dnp3_master.pytest_plugin",
        "--strict-markers",
        "--dnp3-performance-profile",
        str(PERFORMANCE),
        "--dnp3-local-event-profile",
        str(EVENTS),
    )
    result.assert_outcomes(passed=1)
