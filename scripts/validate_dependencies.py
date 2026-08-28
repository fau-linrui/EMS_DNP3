#!/usr/bin/env python3
"""Verify fixed OpenDNP3 build dependencies without accessing the network."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import AbstractSet, Any


REQUIRED_DEPENDENCIES = frozenset({"asio", "exe4cpp", "ser4cpp"})
HASH_PATTERNS = {
    "sha1": re.compile(r"^[0-9a-f]{40}$"),
    "sha256": re.compile(r"^[0-9a-f]{64}$"),
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    dependency: str | None = None

    def render(self) -> str:
        location = "dependency lock"
        if self.dependency:
            location += f" [{self.dependency}]"
        return f"{location}: {self.code}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    lock_path: Path
    dependency_count: int
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_tree_sha256(root: Path | str) -> str:
    """Hash sorted relative paths and contents for a deterministic source-tree seal."""

    source_root = Path(root)
    digest = hashlib.sha256()
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(source_root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve_safe_path(
    value: object,
    *,
    project_root: Path,
    expected_prefix: tuple[str, ...],
    dependency: str,
    field_name: str,
) -> tuple[Path | None, ValidationIssue | None]:
    if not isinstance(value, str) or not value:
        return None, ValidationIssue(
            "INVALID_PATH", f"{field_name} must be a non-empty string", dependency
        )
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or pure.parts[: len(expected_prefix)] != expected_prefix:
        return None, ValidationIssue(
            "UNSAFE_PATH",
            f"{field_name} must remain under {'/'.join(expected_prefix)}: {value!r}",
            dependency,
        )
    candidate = project_root.joinpath(*pure.parts).resolve(strict=False)
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return None, ValidationIssue(
            "UNSAFE_PATH", f"{field_name} escapes the project root: {value!r}", dependency
        )
    return candidate, None


def _valid_hash(value: object, algorithm: str) -> bool:
    return isinstance(value, str) and HASH_PATTERNS[algorithm].fullmatch(value) is not None


def validate_lock(
    lock_path: Path | str,
    *,
    project_root: Path | str | None = None,
    required_names: AbstractSet[str] = REQUIRED_DEPENDENCIES,
) -> ValidationResult:
    lock = Path(lock_path).resolve()
    root = Path(project_root).resolve() if project_root else lock.parent.parent.resolve()
    issues: list[ValidationIssue] = []

    try:
        payload = json.loads(
            lock.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        return ValidationResult(lock, 0, (ValidationIssue("LOCK_READ_ERROR", str(error)),))
    if not isinstance(payload, dict):
        return ValidationResult(
            lock, 0, (ValidationIssue("INVALID_LOCK_ROOT", "root must be an object"),)
        )
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        issues.append(ValidationIssue("INVALID_SCHEMA_VERSION", "schema_version must be 1"))
    if payload.get("build_policy") != "vendored-offline-no-network-fetch":
        issues.append(
            ValidationIssue(
                "INVALID_BUILD_POLICY",
                "build_policy must prohibit network fetches",
            )
        )
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        issues.append(ValidationIssue("INVALID_DEPENDENCIES", "dependencies must be an array"))
        return ValidationResult(lock, 0, tuple(issues))

    seen: set[str] = set()
    for item in dependencies:
        if not isinstance(item, dict):
            issues.append(ValidationIssue("INVALID_DEPENDENCY", "entry must be an object"))
            continue
        name = item.get("name")
        dependency = name if isinstance(name, str) and name else None
        if dependency is None:
            issues.append(ValidationIssue("INVALID_DEPENDENCY_NAME", "name is required"))
            continue
        if dependency in seen:
            issues.append(
                ValidationIssue("DUPLICATE_DEPENDENCY", "name occurs more than once", dependency)
            )
        seen.add(dependency)

        if not isinstance(item.get("version"), str) or not item["version"]:
            issues.append(
                ValidationIssue("MISSING_VERSION", "version/revision is required", dependency)
            )
        source_url = item.get("source_url")
        if not isinstance(source_url, str) or not source_url.startswith("https://"):
            issues.append(
                ValidationIssue("INVALID_SOURCE_URL", "source_url must use HTTPS", dependency)
            )
        if item.get("locally_staged") is not True:
            issues.append(
                ValidationIssue(
                    "NOT_LOCALLY_STAGED", "locally_staged must be true", dependency
                )
            )

        archive, path_issue = _resolve_safe_path(
            item.get("archive_path"),
            project_root=root,
            expected_prefix=("third_party", "distfiles"),
            dependency=dependency,
            field_name="archive_path",
        )
        if path_issue:
            issues.append(path_issue)
        elif archive is not None and not archive.is_file():
            issues.append(
                ValidationIssue("MISSING_ARCHIVE", f"file does not exist: {archive}", dependency)
            )
        elif archive is not None:
            for algorithm, field_name, issue_code in (
                ("sha1", "archive_sha1", "ARCHIVE_SHA1_MISMATCH"),
                ("sha256", "archive_sha256", "ARCHIVE_SHA256_MISMATCH"),
            ):
                expected = item.get(field_name)
                if not _valid_hash(expected, algorithm) or _hash_file(archive, algorithm) != expected:
                    issues.append(
                        ValidationIssue(issue_code, f"{field_name} does not match", dependency)
                    )

        source, path_issue = _resolve_safe_path(
            item.get("source_path"),
            project_root=root,
            expected_prefix=("third_party",),
            dependency=dependency,
            field_name="source_path",
        )
        if path_issue:
            issues.append(path_issue)
        elif source is not None and not source.is_dir():
            issues.append(
                ValidationIssue(
                    "MISSING_SOURCE_TREE", f"directory does not exist: {source}", dependency
                )
            )
        elif source is not None:
            if any(path.is_symlink() for path in source.rglob("*")):
                issues.append(
                    ValidationIssue(
                        "SOURCE_TREE_SYMLINK", "source tree must not contain symlinks", dependency
                    )
                )
            expected_tree_hash = item.get("source_tree_sha256")
            if (
                not _valid_hash(expected_tree_hash, "sha256")
                or compute_tree_sha256(source) != expected_tree_hash
            ):
                issues.append(
                    ValidationIssue(
                        "SOURCE_TREE_SHA256_MISMATCH",
                        "source_tree_sha256 does not match",
                        dependency,
                    )
                )

        licenses = item.get("licenses")
        if not isinstance(licenses, list) or not licenses:
            issues.append(
                ValidationIssue("MISSING_LICENSE_RECORD", "licenses must not be empty", dependency)
            )
            continue
        for license_record in licenses:
            if not isinstance(license_record, dict):
                issues.append(
                    ValidationIssue("INVALID_LICENSE_RECORD", "license must be an object", dependency)
                )
                continue
            if not isinstance(license_record.get("spdx"), str) or not license_record["spdx"]:
                issues.append(
                    ValidationIssue("INVALID_LICENSE_SPDX", "SPDX identifier is required", dependency)
                )
            license_path, path_issue = _resolve_safe_path(
                license_record.get("path"),
                project_root=root,
                expected_prefix=("LICENSES",),
                dependency=dependency,
                field_name="license path",
            )
            if path_issue:
                issues.append(path_issue)
            elif license_path is not None and not license_path.is_file():
                issues.append(
                    ValidationIssue(
                        "MISSING_LICENSE_FILE",
                        f"file does not exist: {license_path}",
                        dependency,
                    )
                )
            elif license_path is not None:
                expected = license_record.get("sha256")
                if not _valid_hash(expected, "sha256") or _hash_file(license_path, "sha256") != expected:
                    issues.append(
                        ValidationIssue(
                            "LICENSE_SHA256_MISMATCH", "license SHA-256 does not match", dependency
                        )
                    )

    for missing in sorted(set(required_names) - seen):
        issues.append(
            ValidationIssue("MISSING_REQUIRED_DEPENDENCY", "required entry is absent", missing)
        )
    return ValidationResult(lock, len(dependencies), tuple(issues))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path, help="path to dependency lock JSON")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = validate_lock(args.lock, project_root=args.project_root)
    if args.json:
        print(
            json.dumps(
                {
                    "lock": str(result.lock_path),
                    "ok": result.ok,
                    "dependency_count": result.dependency_count,
                    "issues": [asdict(issue) for issue in result.issues],
                },
                indent=2,
            )
        )
    elif result.ok:
        print(f"PASS: {result.dependency_count} offline dependencies validated")
    else:
        for issue in result.issues:
            print(issue.render(), file=sys.stderr)
        print(f"FAIL: {len(result.issues)} dependency issue(s)", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
