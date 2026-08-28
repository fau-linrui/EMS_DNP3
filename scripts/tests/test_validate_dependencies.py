from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.validate_dependencies import compute_tree_sha256, validate_lock


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
