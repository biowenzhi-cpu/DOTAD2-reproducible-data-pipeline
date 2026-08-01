from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest


@pytest.fixture
def workbook_with_header_blank_and_record(tmp_path: Path) -> Path:
    path = tmp_path / "rows.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "metadata"
    sheet.append(["antibody_id", "name", None, "name"])
    sheet.append([None, None, None, None])
    sheet.append(["DOTAD-001", "example", "blank-header-value", "alias"])
    workbook.save(path)
    return path
