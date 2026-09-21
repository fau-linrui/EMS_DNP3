#!/usr/bin/env python3
"""Verify fixed OpenDNP3 build dependencies without accessing the network."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import AbstractSet, Any
import zipfile


REQUIRED_DEPENDENCIES = frozenset({"asio", "exe4cpp", "ser4cpp"})
HASH_PATTERNS = {
    "sha1": re.compile(r"^[0-9a-f]{40}$"),
    "sha256": re.compile(r"^[0-9a-f]{64}$"),
}
_MAX_LOCK_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_TREE_BYTES = 256 * 1024 * 1024
_MAX_TREE_ENTRIES = 16384


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


def _fixed_path(root: Path, relative: str) -> Path:
    """Reject links before opening any member of a fixed source tree."""
    if not relative or "\\" in relative or ":" in relative or any(
        part in {"", ".", ".."} for part in relative.split("/")
    ):
        raise ValueError(f"unsafe fixed-source path: {relative!r}")
    path = root
    for part in relative.split("/"):
        path /= part
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"fixed source contains a symlink/reparse point: {relative}")
    return path


def _bounded_bytes(path: Path, maximum: int = _MAX_SOURCE_BYTES) -> bytes:
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"fixed source is not a bounded regular file: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"fixed source grew beyond its byte limit: {path.name}")
    return data


def _text_forms(data: bytes) -> tuple[bytes, ...]:
    """Allow only Git's CRLF/LF conversion, never whitespace/content changes."""
    if b"\0" in data:
        return (data,)
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeError:
        return (data,)
    lf = data.replace(b"\r\n", b"\n")
    return (data, lf, lf.replace(b"\n", b"\r\n"))


def _same_source_bytes(actual: bytes, reference: bytes) -> bool:
    if actual == reference:
        return True
    actual_forms, reference_forms = _text_forms(actual), _text_forms(reference)
    return len(actual_forms) > 1 and len(reference_forms) > 1 and actual_forms[1] == reference_forms[1]


def _matches_locked_text(data: bytes, digest: object, size: object = None) -> bool:
    if not _valid_hash(digest, "sha256"):
        return False
    if size is not None and (type(size) is not int or size < 0):
        return False
    return any(
        (size is None or len(form) == size) and hashlib.sha256(form).hexdigest() == digest
        for form in _text_forms(data)
    )


def _fixed_lock(root: Path, filename: str) -> dict[str, Any]:
    path = _fixed_path(root, f"third_party/{filename}")
    document = json.loads(
        _bounded_bytes(path, _MAX_LOCK_BYTES).decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(document, dict):
        raise ValueError(f"{filename} must contain an object")
    return document


def _opendnp3_issues(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        lock = _fixed_lock(root, "opendnp3.lock.json")
        if lock.get("version") != "3.1.2" or lock.get("commit") != "26b4c01e4839bbbda8866655e086471c4917ee53":
            raise ValueError("OpenDNP3 identity must remain the reviewed 3.1.2 commit")
        archive_record = lock["source_archive"]
        if archive_record["path"] != "third_party/distfiles/opendnp3-3.1.2.zip":
            raise ValueError("OpenDNP3 archive path differs from the fixed path")
        if lock["checkout"]["path"] != "third_party/opendnp3":
            raise ValueError("OpenDNP3 source path differs from the fixed path")
        archive_path = _fixed_path(root, archive_record["path"])
        archive_bytes = _bounded_bytes(archive_path, _MAX_ARCHIVE_BYTES)
        if (
            type(archive_record["bytes"]) is not int
            or len(archive_bytes) != archive_record["bytes"]
            or not _valid_hash(archive_record["sha256"], "sha256")
            or hashlib.sha256(archive_bytes).hexdigest() != archive_record["sha256"]
        ):
            raise ValueError("OpenDNP3 archive size/SHA-256 differs from its lock")
        # The archive itself is verified before trusting any source member.
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.infolist()
            if len(members) > _MAX_TREE_ENTRIES or len(members) != archive_record["entries"]:
                raise ValueError("OpenDNP3 archive entry count differs from its lock or exceeds the limit")
            expected: dict[str, zipfile.ZipInfo] = {}
            seen_paths: set[str] = set()
            total_bytes = 0
            for member in members:
                if not member.filename.startswith("opendnp3-3.1.2/"):
                    raise ValueError("OpenDNP3 archive has an unexpected root")
                relative = member.filename[len("opendnp3-3.1.2/"):].rstrip("/")
                if not relative and member.is_dir():
                    continue
                if (
                    "\\" in relative or ":" in relative
                    or any(part in {"", ".", "..", ".git"} for part in relative.split("/"))
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    raise ValueError("OpenDNP3 archive contains an unsafe path/link")
                if member.is_dir():
                    continue
                if relative.casefold() in seen_paths:
                    raise ValueError("OpenDNP3 archive contains duplicate/case-colliding paths")
                seen_paths.add(relative.casefold())
                total_bytes += member.file_size
                if member.file_size > _MAX_SOURCE_BYTES or total_bytes > _MAX_TREE_BYTES:
                    raise ValueError("OpenDNP3 archive exceeds its decompressed byte limit")
                expected[relative] = member
            source_root = _fixed_path(root, "third_party/opendnp3")
            actual: dict[str, Path] = {}
            actual_entries = 0
            actual_bytes = 0
            for base, directories, filenames in os.walk(source_root, followlinks=False):
                if Path(base) == source_root:
                    # A pre-existing upstream checkout's metadata is not source.
                    directories[:] = [name for name in directories if name != ".git"]
                    filenames = [name for name in filenames if name != ".git"]
                for name in directories + filenames:
                    actual_entries += 1
                    if actual_entries > _MAX_TREE_ENTRIES:
                        raise ValueError("OpenDNP3 source tree exceeds its entry limit")
                    relative = (Path(base) / name).relative_to(source_root).as_posix()
                    _fixed_path(root, f"third_party/opendnp3/{relative}")
                for name in filenames:
                    path = Path(base) / name
                    actual_bytes += path.stat().st_size
                    if actual_bytes > _MAX_TREE_BYTES:
                        raise ValueError("OpenDNP3 source tree exceeds its byte limit")
                    actual[path.relative_to(source_root).as_posix()] = path
            for relative in sorted(set(expected) - set(actual)):
                issues.append(ValidationIssue("MISSING_SOURCE_FILE", relative, "opendnp3"))
            for relative in sorted(set(actual) - set(expected)):
                issues.append(ValidationIssue("EXTRA_SOURCE_FILE", relative, "opendnp3"))
            for relative in sorted(set(actual) & set(expected)):
                reference = archive.read(expected[relative])
                if not _same_source_bytes(_bounded_bytes(actual[relative]), reference):
                    issues.append(ValidationIssue("SOURCE_FILE_MISMATCH", relative, "opendnp3"))
            for label, source_name in (("license", "LICENSE"), ("notice", "NOTICE")):
                record = lock[label]
                if record["source_path"] != f"third_party/opendnp3/{source_name}" or record["copied_path"] != f"LICENSES/opendnp3/{source_name}":
                    raise ValueError(f"OpenDNP3 {label} paths differ from the fixed paths")
                reference = archive.read(expected[source_name])
                if not _matches_locked_text(reference, record["sha256"]):
                    issues.append(ValidationIssue("LICENSE_LOCK_MISMATCH", label, "opendnp3"))
                for field in ("source_path", "copied_path"):
                    path = _fixed_path(root, record[field])
                    if not _same_source_bytes(_bounded_bytes(path), reference):
                        issues.append(ValidationIssue("LICENSE_SHA256_MISMATCH", record[field], "opendnp3"))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        issues.append(ValidationIssue("OPENDNP3_SOURCE_INTEGRITY", str(error), "opendnp3"))
    return issues


def _nlohmann_issues(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        lock = _fixed_lock(root, "nlohmann_json.lock.json")
        if lock.get("build_policy") != "vendored-offline-no-network-fetch":
            raise ValueError("nlohmann/json lock must enforce offline builds")
        include_root = _fixed_path(root, "third_party/nlohmann_json/single_include")
        directories = [include_root]
        include_entries = 0
        while directories:
            for path in directories.pop().iterdir():
                include_entries += 1
                if include_entries > _MAX_TREE_ENTRIES:
                    raise ValueError("nlohmann/json include tree exceeds its entry limit")
                relative = path.relative_to(include_root).as_posix()
                _fixed_path(root, f"third_party/nlohmann_json/single_include/{relative}")
                if relative == "nlohmann" and path.is_dir():
                    directories.append(path)
                elif relative != "nlohmann/json.hpp" or not path.is_file():
                    # Unexpected include names (e.g. array/vector/windows.h) can
                    # shadow system headers. Never open or descend into them.
                    issues.append(ValidationIssue("EXTRA_INCLUDE_PATH", relative, "nlohmann_json"))
        for label, expected in (
            ("artifact", "third_party/nlohmann_json/single_include/nlohmann/json.hpp"),
            ("license", "LICENSES/nlohmann_json/LICENSE.MIT"),
        ):
            record = lock[label]
            if record["local_path"] != expected:
                raise ValueError(f"nlohmann/json {label} path differs from the fixed path")
            data = _bounded_bytes(_fixed_path(root, expected))
            size = record["size_bytes"] if label == "artifact" else None
            if not _matches_locked_text(data, record["sha256"], size):
                issues.append(ValidationIssue("FIXED_FILE_SHA256_MISMATCH", expected, "nlohmann_json"))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        issues.append(ValidationIssue("NLOHMANN_SOURCE_INTEGRITY", str(error), "nlohmann_json"))
    return issues


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


def validate_project_dependencies(
    lock_path: Path | str, *, project_root: Path | str | None = None,
) -> ValidationResult:
    """Validate all five fixed build inputs, not only OpenDNP3's dependencies."""
    result = validate_lock(lock_path, project_root=project_root)
    root = Path(project_root).resolve() if project_root else result.lock_path.parent.parent
    issues = (*result.issues, *_opendnp3_issues(root), *_nlohmann_issues(root))
    return ValidationResult(result.lock_path, result.dependency_count + 2, issues)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = validate_project_dependencies(args.lock, project_root=args.project_root)
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
