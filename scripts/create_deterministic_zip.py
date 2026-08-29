#!/usr/bin/env python3
"""Create a sorted, fixed-metadata ZIP plus a SHA-256 sidecar.

The staged file content still reflects the selected native build.  This script
makes the archive byte-for-byte stable whenever that staged content is stable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Sequence
import zipfile


PACKAGE_MANIFEST_NAME = "package-manifest.json"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_EXECUTABLE_SUFFIXES = frozenset({".exe", ".bat", ".cmd", ".ps1"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _staged_files(source_directory: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in source_directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"package staging tree must not contain symlinks: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError(f"unsupported filesystem entry in staging tree: {path}")
    return tuple(
        sorted(files, key=lambda item: item.relative_to(source_directory).as_posix())
    )


def write_package_manifest(source_directory: Path, package_version: str) -> Path:
    manifest_path = source_directory / PACKAGE_MANIFEST_NAME
    if manifest_path.exists():
        manifest_path.unlink()
    entries = []
    for path in _staged_files(source_directory):
        relative = path.relative_to(source_directory).as_posix()
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "package_version": package_version,
        "manifest_scope": "all packaged files except package-manifest.json",
        "files": entries,
    }
    encoded = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    manifest_path.write_text(encoded, encoding="utf-8", newline="\n")
    return manifest_path


def create_archive(
    source_directory: Path,
    archive_path: Path,
    *,
    package_version: str,
) -> tuple[Path, Path, str]:
    source = source_directory.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"package source is not a directory: {source}")
    archive = archive_path.expanduser().resolve(strict=False)
    try:
        archive.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("archive output must not be inside the staged package tree")
    archive.parent.mkdir(parents=True, exist_ok=True)
    write_package_manifest(source, package_version)
    files = _staged_files(source)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as package:
            for path in files:
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=_FIXED_ZIP_TIME)
                info.create_system = 3
                mode = (
                    0o755
                    if path.suffix.lower() in _EXECUTABLE_SUFFIXES
                    else 0o644
                )
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as stream:
                    package.writestr(info, stream.read(), compresslevel=9)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)

    digest = sha256_file(archive)
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive, sidecar, digest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a deterministic portable DNP3 integration ZIP"
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--package-version", required=True)
    arguments = parser.parse_args(argv)
    archive, sidecar, digest = create_archive(
        arguments.source,
        arguments.output,
        package_version=arguments.package_version,
    )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "sha256_file": str(sidecar),
                "sha256": digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
