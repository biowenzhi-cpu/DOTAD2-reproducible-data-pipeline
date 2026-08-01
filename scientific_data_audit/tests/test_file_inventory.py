import zipfile

import pytest

from dotad_audit.archive_inspector import inspect_zip
from dotad_audit.file_inventory import (
    duplicate_groups,
    inventory_selected_files,
    sha256_file,
)


def test_zip_path_traversal_detection(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("safe/data.csv", "a,b\n1,2\n")
        handle.writestr("../escape.txt", "blocked")
        handle.writestr("/absolute.txt", "blocked")
    rows = inspect_zip(archive)
    statuses = {row["member_path"]: row["security_status"] for row in rows}
    assert statuses["safe/data.csv"] == "safe"
    assert statuses["../escape.txt"] == "unsafe"
    assert statuses["/absolute.txt"] == "unsafe"


def test_sha256_is_stable(tmp_path):
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"DOTAD audit fixture\0\x01")
    first = sha256_file(path, chunk_size=3)
    second = sha256_file(path, chunk_size=11)
    assert first == second
    assert len(first) == 64


def test_exact_duplicate_grouping():
    rows = [
        {"relative_path": "a/data.csv", "filename": "data.csv", "sha256": "abc"},
        {"relative_path": "b/copy.csv", "filename": "copy.csv", "sha256": "abc"},
        {"relative_path": "c/data.csv", "filename": "data.csv", "sha256": "def"},
    ]
    groups = duplicate_groups(rows)
    exact = [row for row in groups if row["relationship_type"] == "exact_duplicate"]
    same_name = [row for row in groups if row["relationship_type"] == "same_name_different_content"]
    assert len(exact) == 1
    assert exact[0]["file_count"] == 2
    assert len(same_name) == 1


def test_selected_file_inventory_excludes_unlisted_files(tmp_path):
    selected = tmp_path / "selected.xlsx"
    ignored = tmp_path / "ignored.csv"
    selected.write_bytes(b"selected")
    ignored.write_bytes(b"ignored")

    rows = inventory_selected_files(tmp_path, [selected], {})

    assert [row["relative_path"] for row in rows] == ["selected.xlsx"]


def test_selected_file_inventory_rejects_path_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="outside project root"):
            inventory_selected_files(tmp_path, [outside], {})
    finally:
        outside.unlink(missing_ok=True)
