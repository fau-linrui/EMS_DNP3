"""Strict verification of an unpacked portable integration package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Sequence


PACKAGE_MANIFEST_NAME = "package-manifest.json"
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class PackageVerificationError(RuntimeError):
    """The unpacked package does not exactly match its signed-off manifest."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("manifest paths must be non-empty POSIX paths")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"manifest path is unsafe: {value!r}")
    if ":" in pure.parts[0]:
        raise ValueError(f"manifest path has a drive prefix: {value!r}")
    return pure.as_posix()


def verify_unpacked_package(root_directory: Path | str) -> dict[str, object]:
    root = Path(root_directory).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise PackageVerificationError(f"package root is not a directory: {root}")
    manifest_path = root / PACKAGE_MANIFEST_NAME
    try:
        size = manifest_path.stat().st_size
        if size <= 0 or size > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest size is outside the allowed range")
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PackageVerificationError(f"package manifest is invalid: {error}") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "package_version",
        "manifest_scope",
        "files",
    }:
        raise PackageVerificationError("package manifest has missing or unknown fields")
    if manifest["schema_version"] != 1:
        raise PackageVerificationError("package manifest schema_version must be 1")
    version = manifest["package_version"]
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise PackageVerificationError("package_version must use major.minor.patch")
    if manifest["manifest_scope"] != "all packaged files except package-manifest.json":
        raise PackageVerificationError("package manifest_scope is invalid")
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise PackageVerificationError("package manifest files must be a non-empty array")

    expected: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise PackageVerificationError("package manifest contains an invalid file entry")
        try:
            relative = _safe_relative_path(entry["path"])
        except ValueError as error:
            raise PackageVerificationError(str(error)) from error
        if relative == PACKAGE_MANIFEST_NAME:
            raise PackageVerificationError("package manifest must not hash itself")
        size_bytes = entry["size_bytes"]
        sha256 = entry["sha256"]
        if type(size_bytes) is not int or size_bytes < 0:
            raise PackageVerificationError(f"invalid size for {relative}")
        if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
            raise PackageVerificationError(f"invalid SHA-256 for {relative}")
        if relative in expected:
            raise PackageVerificationError(f"duplicate package path: {relative}")
        expected[relative] = (size_bytes, sha256)

    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PackageVerificationError(f"package contains a symlink: {path.name}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative != PACKAGE_MANIFEST_NAME:
                actual[relative] = path
        elif not path.is_dir():
            raise PackageVerificationError(f"package contains an unsupported entry: {path.name}")

    missing = sorted(set(expected).difference(actual))
    extra = sorted(set(actual).difference(expected))
    if missing or extra:
        raise PackageVerificationError(
            "package file set differs from manifest"
            + (f"; missing={missing}" if missing else "")
            + (f"; extra={extra}" if extra else "")
        )
    for relative, path in actual.items():
        expected_size, expected_hash = expected[relative]
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise PackageVerificationError(
                f"package file size mismatch: {relative}"
            )
        if _sha256_file(path) != expected_hash:
            raise PackageVerificationError(
                f"package file SHA-256 mismatch: {relative}"
            )
    return {
        "ok": True,
        "package_version": version,
        "verified_files": len(actual),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an unpacked DNP3 package")
    parser.add_argument("--root", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = verify_unpacked_package(arguments.root)
    except Exception as error:
        print(f"PACKAGE VERIFICATION FAILED: {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
