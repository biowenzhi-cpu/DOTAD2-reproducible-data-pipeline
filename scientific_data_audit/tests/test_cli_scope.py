from __future__ import annotations

from pathlib import Path

import pytest

from dotad_audit.cli import (
    AUTHORITATIVE_DATA_RELATIVE_PATHS,
    resolve_selected_data_files,
)


def create_authoritative_files(root: Path) -> list[Path]:
    paths = []
    for relative in AUTHORITATIVE_DATA_RELATIVE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        paths.append(Path(relative))
    return paths


def test_authoritative_scope_accepts_exact_five_files(tmp_path: Path) -> None:
    values = create_authoritative_files(tmp_path)

    resolved = resolve_selected_data_files(tmp_path, values)

    assert {
        path.relative_to(tmp_path).as_posix() for path in resolved
    } == AUTHORITATIVE_DATA_RELATIVE_PATHS


def test_authoritative_scope_rejects_empty_file_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must exactly match"):
        resolve_selected_data_files(tmp_path, [])


def test_authoritative_scope_rejects_missing_file(tmp_path: Path) -> None:
    values = create_authoritative_files(tmp_path)

    with pytest.raises(ValueError, match="must exactly match"):
        resolve_selected_data_files(tmp_path, values[:-1])


def test_authoritative_scope_rejects_extra_file(tmp_path: Path) -> None:
    values = create_authoritative_files(tmp_path)
    extra = tmp_path / "assets" / "data" / "unexpected.xlsx"
    extra.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="unexpected"):
        resolve_selected_data_files(tmp_path, [*values, extra])
