from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts.validate_dependencies import (
    _nlohmann_issues, _opendnp3_issues, _same_source_bytes,
    compute_tree_sha256, validate_lock, validate_project_dependencies,
)
import scripts.validate_dependencies as dependency_validator


def digest(path: Path, algorithm: str) -> str:
    value = hashlib.new(algorithm)
    value.update(path.read_bytes())
    return value.hexdigest()


def create_lock(project_root: Path) -> Path:
    archive = project_root / "third_party" / "distfiles" / "asio.zip"
    source = project_root / "third_party" / "asio"
    license_file = project_root / "LICENSES" / "asio" / "LICENSE_1_0.txt"
    archive.parent.mkdir(parents=True)
    source.mkdir(parents=True)
    license_file.parent.mkdir(parents=True)
    archive.write_bytes(b"fixed archive")
    (source / "asio.hpp").write_text("// fixed source\n", encoding="utf-8")
    license_file.write_text("Boost Software License\n", encoding="utf-8")

    lock = {
        "schema_version": 1,
        "build_policy": "vendored-offline-no-network-fetch",
        "dependencies": [
            {
                "name": "asio",
                "version": "asio-1-16-0",
                "source_url": "https://example.invalid/asio.zip",
                "archive_path": "third_party/distfiles/asio.zip",
                "archive_sha1": digest(archive, "sha1"),
                "archive_sha256": digest(archive, "sha256"),
                "source_path": "third_party/asio",
                "source_tree_sha256": compute_tree_sha256(source),
                "licenses": [
                    {
                        "spdx": "BSL-1.0",
                        "path": "LICENSES/asio/LICENSE_1_0.txt",
                        "sha256": digest(license_file, "sha256"),
                    }
                ],
                "locally_staged": True,
            }
        ],
    }
    lock_path = project_root / "third_party" / "opendnp3-dependencies.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path


def issue_codes(lock_path: Path) -> set[str]:
    result = validate_lock(
        lock_path,
        project_root=lock_path.parents[1],
        required_names={"asio"},
    )
    return {issue.code for issue in result.issues}


def test_valid_fixed_dependency_passes(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)

    result = validate_lock(
        lock_path,
        project_root=tmp_path,
        required_names={"asio"},
    )

    assert result.ok, [issue.render() for issue in result.issues]
    assert result.dependency_count == 1


def test_archive_corruption_is_rejected(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    archive = tmp_path / "third_party" / "distfiles" / "asio.zip"
    archive.write_bytes(b"corrupted")

    codes = issue_codes(lock_path)

    assert "ARCHIVE_SHA1_MISMATCH" in codes
    assert "ARCHIVE_SHA256_MISMATCH" in codes


def test_source_tree_corruption_is_rejected(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    source = tmp_path / "third_party" / "asio" / "asio.hpp"
    source.write_text("// modified\n", encoding="utf-8")

    assert "SOURCE_TREE_SHA256_MISMATCH" in issue_codes(lock_path)


def test_missing_license_is_rejected(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    license_file = tmp_path / "LICENSES" / "asio" / "LICENSE_1_0.txt"
    license_file.unlink()

    assert "MISSING_LICENSE_FILE" in issue_codes(lock_path)


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["dependencies"][0]["source_path"] = "../outside"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    assert "UNSAFE_PATH" in issue_codes(lock_path)


def test_duplicate_dependency_name_is_rejected(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["dependencies"].append(dict(lock["dependencies"][0]))
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    assert "DUPLICATE_DEPENDENCY" in issue_codes(lock_path)


def create_direct_locks(root: Path) -> None:
    third_party = root / "third_party"
    archive = third_party / "distfiles" / "opendnp3-3.1.2.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    files = {"source.cpp": b"// fixed\n", "LICENSE": b"licensed\n", "NOTICE": b"notice\n", "binary.png": b"\x00\r\n"}
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in files.items():
            output.writestr(f"opendnp3-3.1.2/{name}", content)
            path = third_party / "opendnp3" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    lock = {
        "version": "3.1.2", "commit": "26b4c01e4839bbbda8866655e086471c4917ee53",
        "checkout": {"path": "third_party/opendnp3"},
        "source_archive": {"path": "third_party/distfiles/opendnp3-3.1.2.zip", "bytes": archive.stat().st_size, "entries": len(files), "sha256": digest(archive, "sha256")},
    }
    for field, name in (("license", "LICENSE"), ("notice", "NOTICE")):
        copy = root / "LICENSES" / "opendnp3" / name
        copy.parent.mkdir(parents=True, exist_ok=True)
        # Locks can have been recorded from an original Windows CRLF checkout.
        copy.write_bytes(files[name].replace(b"\n", b"\r\n"))
        lock[field] = {"source_path": f"third_party/opendnp3/{name}", "copied_path": f"LICENSES/opendnp3/{name}", "sha256": digest(copy, "sha256")}
    (third_party / "opendnp3.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    json_lock = {"build_policy": "vendored-offline-no-network-fetch"}
    for field, relative in (("artifact", "third_party/nlohmann_json/single_include/nlohmann/json.hpp"), ("license", "LICENSES/nlohmann_json/LICENSE.MIT")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"// fixed json input\n")
        json_lock[field] = {"local_path": relative, "sha256": digest(path, "sha256")}
        if field == "artifact":
            json_lock[field]["size_bytes"] = path.stat().st_size
    (third_party / "nlohmann_json.lock.json").write_text(json.dumps(json_lock), encoding="utf-8")


def test_direct_dependencies_and_license_copies_pass(tmp_path: Path) -> None:
    create_direct_locks(tmp_path)
    assert not _opendnp3_issues(tmp_path)
    assert not _nlohmann_issues(tmp_path)


@pytest.mark.parametrize("mutation,code", [
    ("changed", "SOURCE_FILE_MISMATCH"), ("missing", "MISSING_SOURCE_FILE"),
    ("extra", "EXTRA_SOURCE_FILE"), ("archive", "OPENDNP3_SOURCE_INTEGRITY"),
    ("license", "LICENSE_SHA256_MISMATCH"), ("license_lock", "LICENSE_LOCK_MISMATCH"),
])
def test_opendnp3_drift_is_rejected(tmp_path: Path, mutation: str, code: str) -> None:
    create_direct_locks(tmp_path)
    source = tmp_path / "third_party/opendnp3/source.cpp"
    if mutation == "changed": source.write_bytes(b"// changed\n")
    elif mutation == "missing": source.unlink()
    elif mutation == "extra": source.with_name("extra.cpp").write_bytes(b"// extra\n")
    elif mutation == "archive": (tmp_path / "third_party/distfiles/opendnp3-3.1.2.zip").write_bytes(b"corrupt")
    elif mutation == "license": (tmp_path / "LICENSES/opendnp3/LICENSE").write_bytes(b"changed license")
    else:
        path = tmp_path / "third_party/opendnp3.lock.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        lock["license"]["sha256"] = "0" * 64
        path.write_text(json.dumps(lock), encoding="utf-8")
    assert code in {item.code for item in _opendnp3_issues(tmp_path)}


@pytest.mark.parametrize("relative", ["third_party/nlohmann_json/single_include/nlohmann/json.hpp", "LICENSES/nlohmann_json/LICENSE.MIT"])
def test_nlohmann_source_and_license_drift_are_rejected(tmp_path: Path, relative: str) -> None:
    create_direct_locks(tmp_path)
    (tmp_path / relative).write_bytes(b"modified input")
    assert "FIXED_FILE_SHA256_MISMATCH" in {item.code for item in _nlohmann_issues(tmp_path)}


@pytest.mark.parametrize("name", ["array", "vector", "windows.h", "nlohmann/extra.hpp"])
def test_nlohmann_include_root_cannot_shadow_system_headers(tmp_path: Path, name: str) -> None:
    create_direct_locks(tmp_path)
    (tmp_path / "third_party/nlohmann_json/single_include" / name).write_bytes(b"synthetic extra header")
    assert "EXTRA_INCLUDE_PATH" in {item.code for item in _nlohmann_issues(tmp_path)}


def test_only_utf8_text_line_endings_are_equivalent(tmp_path: Path) -> None:
    create_direct_locks(tmp_path)
    (tmp_path / "third_party/opendnp3/source.cpp").write_bytes(b"// fixed\r\n")
    metadata = tmp_path / "third_party/opendnp3/.git"
    metadata.mkdir()
    (metadata / "config").write_bytes(b"synthetic checkout metadata is not source")
    assert not _opendnp3_issues(tmp_path)
    assert _same_source_bytes(b"// text\n", b"// text\r\n")
    assert not _same_source_bytes(b"// text \n", b"// text\n")
    assert not _same_source_bytes(b"\x00\r\n", b"\x00\n")
    assert not _same_source_bytes(b"\xff\r\n", b"\xff\n")


@pytest.mark.parametrize("name", ["opendnp3-3.1.2/../escape.cpp", "opendnp3-3.1.2/C:/escape.cpp", "unexpected-root/file.cpp"])
def test_archive_paths_are_validated_after_archive_hash(tmp_path: Path, name: str) -> None:
    create_direct_locks(tmp_path)
    archive = tmp_path / "third_party/distfiles/opendnp3-3.1.2.zip"
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr(name, b"synthetic")
    lock_path = tmp_path / "third_party/opendnp3.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["source_archive"].update(bytes=archive.stat().st_size, entries=5, sha256=digest(archive, "sha256"))
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    assert "OPENDNP3_SOURCE_INTEGRITY" in {item.code for item in _opendnp3_issues(tmp_path)}


def test_project_validation_never_skips_missing_direct_locks(tmp_path: Path) -> None:
    lock_path = create_lock(tmp_path)
    codes = {item.code for item in validate_project_dependencies(lock_path).issues}
    assert {"OPENDNP3_SOURCE_INTEGRITY", "NLOHMANN_SOURCE_INTEGRITY"} <= codes


def test_oversized_archive_member_is_rejected_before_decompression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_direct_locks(tmp_path)
    monkeypatch.setattr(dependency_validator, "_MAX_SOURCE_BYTES", 2)
    issues = _opendnp3_issues(tmp_path)
    assert any("decompressed byte limit" in issue.message for issue in issues)


def test_nlohmann_reparse_point_is_rejected_without_reading_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_direct_locks(tmp_path)
    header = tmp_path / "third_party/nlohmann_json/single_include/nlohmann/json.hpp"
    original_stat = Path.lstat
    original_open = Path.open

    def synthetic_reparse(path: Path):
        metadata = original_stat(path)
        if path == header:
            return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
        return metadata

    def guarded_open(path: Path, *args, **kwargs):
        if path == header:
            raise AssertionError("must reject a reparse point before reading its content")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", synthetic_reparse)
    monkeypatch.setattr(Path, "open", guarded_open)
    assert any("reparse point" in issue.message for issue in _nlohmann_issues(tmp_path))
