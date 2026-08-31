from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROBE = (
    REPOSITORY_ROOT
    / "scripts"
    / "fixtures"
    / "pytest_consumer"
    / "compatibility_probe.py"
)


def run_probe(mode: str, *, pythonpath: Path | None = None) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if pythonpath is None:
        environment.pop("PYTHONPATH", None)
    else:
        environment["PYTHONPATH"] = str(pythonpath)
    completed = subprocess.run(
        [sys.executable, str(PROBE), mode],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=REPOSITORY_ROOT,
        timeout=10.0,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_environment_probe_reports_actual_tool_versions() -> None:
    result = run_probe("environment")

    assert result["python"] == ".".join(map(str, sys.version_info[:3]))
    assert isinstance(result["pytest"], str)
    assert isinstance(result["pip"], str)
    assert result["setuptools"] is None or isinstance(result["setuptools"], str)


def test_installed_package_probe_respects_explicit_pythonpath() -> None:
    source = REPOSITORY_ROOT / "python" / "src"
    result = run_probe("installed-package", pythonpath=source)

    imported = Path(str(result["path"])).resolve()
    assert imported.is_relative_to(source.resolve())
    assert result["version"] == "0.6.1"
