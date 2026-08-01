from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuildContext:
    output_dir: Path
    core_inputs: dict[str, Path]
    sheet_roles: dict[str, Any]
    input_shas: dict[str, str]
    release_version: str
    script_root: Path


@dataclass(frozen=True)
class ExportResult:
    table_name: str
    path: Path
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    row_definition: str
