from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.stage_package_sources import (
    NATIVE_FILES, load_plan, source_inputs, stage_sources, verify_stage,
)


def test_repository_release_allowlist_has_valid_unique_sources() -> None:
    root = Path(__file__).resolve().parents[2]
    plan = load_plan(root / "scripts" / "package-source-files.json")
    assert len(source_inputs(root, plan)) == len(plan)
    assert plan["scripts/test-compatibility.ps1"] == "compatibility-test.ps1"
    assert plan["python/src/dnp3_master/client.py"] == "python/src/dnp3_master/client.py"


def test_only_allowlisted_content_is_read_and_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, stage = tmp_path / "repo", tmp_path / "stage"
    examples = source / "examples"
    examples.mkdir(parents=True)
    (examples / "test_demo.py").write_text("# public test", encoding="utf-8")
    # These are synthetic fixtures, never real device data.
    forbidden = [examples / "capture.pcap", examples / "key.pem", examples / "settings.local.json"]
    for path in forbidden:
        path.write_text("SYNTHETIC PRIVATE FIXTURE", encoding="utf-8")
    original_open = Path.open
    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path in forbidden:
            raise AssertionError("unlisted private file must not be opened")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    stage_sources(source, stage, {"examples/test_demo.py": "examples/test_demo.py"})
    assert sorted(path.name for path in (stage / "examples").iterdir()) == ["test_demo.py"]


@pytest.mark.parametrize("path", [
    "../outside.py", "a/../outside.py", "a//b.py", "/absolute.py", "C:/secret.py",
    "examples/capture.pcap", "examples/key.pem", "examples/settings.local.json",
    "secrets/a.txt", "docs/standard.pdf", "python/__pycache__/cache.pyc",
])
def test_manifest_refuses_unsafe_or_private_paths(tmp_path: Path, path: str) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": {path: "safe.py"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_plan(manifest)


def test_manifest_rejects_duplicate_case_insensitive_destinations(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": {"a.py": "file.py", "b.py": "FILE.py"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate package destination"):
        load_plan(manifest)


def test_pre_archive_verification_refuses_extra_files(tmp_path: Path) -> None:
    plan = {"a.py": "python/a.py"}
    expected = NATIVE_FILES | {"python/a.py", "python-dist/dnp3_master_test_framework-0.6.1-py3-none-any.whl"}
    for name in expected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    verify_stage(tmp_path, plan, "0.6.1")
    (tmp_path / "capture.pcap").write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="non-allowlisted"):
        verify_stage(tmp_path, plan, "0.6.1")


def test_missing_source_fails_before_copying_anything(tmp_path: Path) -> None:
    (tmp_path / "public.py").write_text("public", encoding="utf-8")
    stage = tmp_path / "stage"
    with pytest.raises(ValueError, match="missing"):
        stage_sources(tmp_path, stage, {"public.py": "public.py", "absent.py": "absent.py"})
    assert not stage.exists()
