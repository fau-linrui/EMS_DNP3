"""Copy only reviewed release inputs; never recursively copy a source tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Sequence


NATIVE_FILES = frozenset({
    "bin/dnp3-master-host.exe", "bin/build-info.json",
    "tools/dnp3-local-test-outstation.exe",
})
_PRIVATE_SUFFIXES = {".pcap", ".pcapng", ".key", ".pem", ".pfx", ".p12", ".cer", ".crt", ".pdf"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate package source manifest field")
        result[key] = value
    return result


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("package paths must be relative POSIX paths")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("package path must not escape its root")
    lowered = [part.lower() for part in parts]
    if (
        any(part in {"secrets", "evidence", "__pycache__", ".git", ".pytest_cache"} for part in lowered)
        or any(".local." in part or part.endswith(".egg-info") for part in lowered)
        or PurePosixPath(value).suffix.lower() in _PRIVATE_SUFFIXES | {".pyc", ".pyo"}
    ):
        raise ValueError("private/generated input is forbidden in a source allowlist")
    return value


def _check_components(root: Path, relative: str) -> Path:
    path = root
    for part in ("", *PurePosixPath(relative).parts):
        if part:
            path = path / part
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("package input/output contains a reparse point or symlink")
    return path


def load_plan(manifest: Path) -> dict[str, str]:
    if not 0 < manifest.stat().st_size <= 1024 * 1024:
        raise ValueError("package source manifest exceeds its size limit")
    document = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if (
        not isinstance(document, dict) or set(document) != {"schema_version", "files"}
        or type(document["schema_version"]) is not int or document["schema_version"] != 1
        or not isinstance(document["files"], dict) or not 1 <= len(document["files"]) <= 4096
    ):
        raise ValueError("invalid package source manifest")
    plan = {_relative(source): _relative(target) for source, target in document["files"].items()}
    destinations = [target.lower() for target in plan.values()]
    if len(set(destinations)) != len(destinations):
        raise ValueError("duplicate package destination")
    if any(target in NATIVE_FILES or target.startswith("python-dist/") for target in destinations):
        raise ValueError("source manifest must not replace generated artifacts")
    return plan


def source_inputs(repository: Path, plan: dict[str, str]) -> list[tuple[Path, str]]:
    inputs = []
    for source, destination in plan.items():
        path = _check_components(repository, source)
        if not path.is_file():
            raise ValueError(f"allowlisted package source is missing: {source}")
        inputs.append((path, destination))
    return inputs


def stage_sources(repository: Path, stage: Path, plan: dict[str, str]) -> None:
    inputs = source_inputs(repository, plan)
    # Validate all paths before reading or copying any source content.
    outputs = [_check_components(stage, destination) for _, destination in inputs]
    for (source, _), target in zip(inputs, outputs):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def verify_stage(stage: Path, plan: dict[str, str], version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("invalid package version")
    expected = set(plan.values()) | NATIVE_FILES | {
        f"python-dist/dnp3_master_test_framework-{version}-py3-none-any.whl"
    }
    actual = set()
    allowed_directories = {
        parent.as_posix()
        for filename in expected for parent in PurePosixPath(filename).parents
    }
    # Reject unexpected files by name before opening them or hashing contents.
    directories = [stage]
    while directories:
        for path in directories.pop().iterdir():
            relative = path.relative_to(stage).as_posix()
            _check_components(stage, relative)
            if path.is_dir():
                if relative not in allowed_directories:
                    raise ValueError("staged package contains a non-allowlisted directory")
                directories.append(path)
            elif path.is_file() and relative in expected:
                actual.add(relative)
            else:
                raise ValueError("staged package contains a non-allowlisted entry")
    if actual != expected:
        raise ValueError("staged package is missing allowlisted files")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify-stage", action="store_true")
    parser.add_argument("--version")
    args = parser.parse_args(argv)
    plan = load_plan(args.manifest)
    if args.check_only:
        source_inputs(args.repository, plan)
    elif args.verify_stage:
        if args.stage is None or args.version is None:
            parser.error("stage verification requires --stage and --version")
        verify_stage(args.stage, plan, args.version)
    else:
        if args.stage is None:
            parser.error("copying requires --stage")
        stage_sources(args.repository, args.stage, plan)
    print(f"PASS: {len(plan)} allowlisted package sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
