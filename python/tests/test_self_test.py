from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_loopback_self_test_covers_packaged_read_and_control_path() -> None:
    host = os.environ.get("DNP3_MASTER_HOST_EXE")
    outstation = os.environ.get("DNP3_TEST_OUTSTATION_EXE")
    assert host and outstation

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "dnp3_master.self_test",
            "--host-exe",
            str(Path(host)),
            "--outstation-exe",
            str(Path(outstation)),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15.0,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["backend"] == "opendnp3"
    assert result["backend_version"] == "3.1.2"
    assert result["integrity_measurements"] >= 9
    assert result["analog_value"] == 123.5
    assert result["command_all_success"] is True
    assert result["command_points"] == 4
    assert result["binary_feedback_cycle"] == [False, True, False]
    assert result["analog_feedback_cycle"] == [0.0, 1.25, 0.0]
    assert result["outstation_operation_count"] == 4
