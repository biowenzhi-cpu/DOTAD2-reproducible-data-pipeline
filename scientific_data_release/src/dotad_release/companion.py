from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from .ids import stable_content_id
from .models import BuildContext, ExportResult
from .workbook_reader import sha256_file, write_tsv


AFFINITY_ARCHIVE_NAME = "dotad_affinity_benchmark_v2.0.zip"
FIELDS = (
    "record_id",
    "source_kind",
    "dataset_file",
    "declared_size",
    "assay_units_as_declared",
    "description_as_declared",
    "publication_as_declared",
    "year_as_declared",
    "favorable_direction_as_declared",
    "direction_interpretation",
    "declared_count",
    "physical_count",
    "physical_file_count",
    "directory_entry_count",
    "total_zip_member_count",
    "count_difference",
    "audit_status",
    "review_reason",
    "inclusion_status",
    "count_scope",
    "core_count_eligible",
    "licence",
    "redistribution_status",
    "source_reference",
    "lineage_sources_json",
)


def build_affinity_companion_manifest(
    context: BuildContext,
) -> ExportResult:
    project_root = _project_root(context)
    archive_path = (
        project_root / "assets" / "data" / AFFINITY_ARCHIVE_NAME
    )
    readme_member, readme_text = _read_archive_readme(archive_path)
    archive_sha = sha256_file(archive_path)
    archive_counts = _archive_member_counts(archive_path)
    rows = [
        _readme_record(
            archive_path,
            archive_sha,
            readme_member,
            source_row,
        )
        for source_row in _parse_markdown_table(readme_text)
    ]
    audit_path = _audit_summary_path(context, project_root)
    rows.extend(_audit_records(archive_path, audit_path, archive_counts))
    for row in rows:
        row.update(archive_counts)

    path = (
        context.output_dir
        / "companion"
        / "affinity_collection_manifest_draft.tsv"
    )
    write_tsv(path, rows, FIELDS)
    return ExportResult(
        table_name="affinity_collection_manifest_draft",
        path=path,
        fieldnames=FIELDS,
        rows=tuple(rows),
        row_definition=(
            "one companion row per affinity README dataset plus each matching "
            "Phase 1 affinity audit review row; no benchmark data rows are read"
        ),
    )


def _read_archive_readme(archive_path: Path) -> tuple[str, str]:
    with ZipFile(archive_path) as archive:
        readme_members = [
            info.filename
            for info in archive.infolist()
            if not info.is_dir()
            and PurePosixPath(info.filename).name.lower() == "readme.md"
        ]
        if len(readme_members) != 1:
            raise ValueError(
                "Affinity archive must contain exactly one README.md"
            )
        member = readme_members[0]
        return member, archive.read(member).decode("utf-8-sig")


def _archive_member_counts(archive_path: Path) -> dict[str, int]:
    with ZipFile(archive_path) as archive:
        members = archive.infolist()
    directory_count = sum(info.is_dir() for info in members)
    return {
        "physical_file_count": len(members) - directory_count,
        "directory_entry_count": directory_count,
        "total_zip_member_count": len(members),
    }


def _parse_markdown_table(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        headers = _split_markdown_row(line)
        normalized = [_normalized_header(value) for value in headers]
        if "filename" not in normalized or "assay_units" not in normalized:
            continue
        rows: list[dict[str, str]] = []
        for source_line, data_line in enumerate(
            lines[index + 2 :],
            start=index + 3,
        ):
            if not data_line.lstrip().startswith("|"):
                break
            values = _split_markdown_row(data_line)
            if not values or _is_separator_row(values):
                continue
            padded = values + [""] * (len(headers) - len(values))
            row = {
                key: padded[column_index].strip()
                for column_index, key in enumerate(normalized)
            }
            row["_source_line"] = str(source_line)
            rows.append(row)
        return rows
    raise ValueError("Affinity README contains no dataset Markdown table")


def _readme_record(
    archive_path: Path,
    archive_sha: str,
    readme_member: str,
    source_row: dict[str, str],
) -> dict[str, Any]:
    dataset_file = source_row.get("filename", "")
    return _common_record(
        {
            "record_id": stable_content_id(
                "AFFCMP", ["README_DATASET", dataset_file]
            ),
            "source_kind": "README_DATASET",
            "dataset_file": dataset_file,
            "declared_size": source_row.get("size", ""),
            "assay_units_as_declared": source_row.get("assay_units", ""),
            "description_as_declared": source_row.get("description", ""),
            "publication_as_declared": source_row.get("publication", ""),
            "year_as_declared": source_row.get("year", ""),
            "favorable_direction_as_declared": source_row.get(
                "direction_of_favorable_values", ""
            ),
            "direction_interpretation": "NEEDS_REVIEW",
            "source_reference": f"{archive_path.name}!{readme_member}",
            "lineage_sources_json": _lineage_json(
                archive_path.name,
                archive_sha,
                readme_member,
                source_row["_source_line"],
            ),
        }
    )


def _audit_records(
    archive_path: Path,
    audit_path: Path,
    archive_counts: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    audit_sha = sha256_file(audit_path)
    audit_reference = _relative_audit_reference(audit_path)
    with audit_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source_line, source_row in enumerate(
            csv.DictReader(handle, delimiter="\t"),
            start=2,
        ):
            if (
                source_row.get("record_type") != "release_file"
                or source_row.get("declared_item") != archive_path.name
                or source_row.get("status", "").lower() == "match"
            ):
                continue
            declared_count = source_row.get(
                "declared_sheet_or_file_count", ""
            )
            physical_count = source_row.get(
                "actual_sheet_or_file_count", ""
            )
            count_difference = source_row.get("count_difference", "")
            declared_number = _as_int(declared_count)
            if declared_number == archive_counts["total_zip_member_count"]:
                count_difference = 0
                audit_status = "match_after_scope_clarification"
                review_reason = (
                    f"Declared count matches total ZIP members: "
                    f"{archive_counts['physical_file_count']} physical files "
                    f"plus {archive_counts['directory_entry_count']} directory "
                    f"entries."
                )
            else:
                audit_status = source_row.get("status", "")
                review_reason = source_row.get("notes", "")
            rows.append(
                _common_record(
                    {
                        "record_id": stable_content_id(
                            "AFFCMP",
                            [
                                "PHASE1_AUDIT_REVIEW",
                                archive_path.name,
                                declared_count,
                                physical_count,
                                count_difference,
                            ],
                        ),
                        "source_kind": "PHASE1_AUDIT_REVIEW",
                        "declared_count": declared_count,
                        "physical_count": physical_count,
                        "count_difference": count_difference,
                        "audit_status": audit_status,
                        "review_reason": review_reason,
                        "source_reference": audit_reference,
                        "lineage_sources_json": _lineage_json(
                            audit_path.name,
                            audit_sha,
                            audit_reference,
                            source_line,
                        ),
                    }
                )
            )
    return rows


def _common_record(values: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {field: "" for field in FIELDS}
    row.update(
        {
            "inclusion_status": "COMPANION_PENDING_REVIEW",
            "count_scope": "COMPANION_ONLY",
            "core_count_eligible": False,
            "licence": "UNKNOWN",
            "redistribution_status": "NEEDS_REVIEW",
        }
    )
    row.update(values)
    return row


def _project_root(context: BuildContext) -> Path:
    dictionary_path = context.core_inputs.get("data_dictionary")
    if dictionary_path is not None:
        for parent in Path(dictionary_path).resolve().parents:
            if parent.name.lower() == "assets":
                return parent.parent
    return Path(context.script_root).resolve()


def _audit_summary_path(
    context: BuildContext,
    input_project_root: Path,
) -> Path:
    relative_path = (
        Path("audit_outputs")
        / "phase1"
        / "03_data_integrity"
        / "data_dictionary_inventory_crosscheck.tsv"
    )
    roots: list[Path] = []
    for seed in (
        Path(context.script_root).resolve(),
        Path(__file__).resolve(),
        input_project_root,
    ):
        for candidate in (seed, *seed.parents):
            if candidate not in roots:
                roots.append(candidate)
    for root in roots:
        candidate = root / relative_path
        if candidate.is_file():
            return candidate
    return roots[0] / relative_path


def _relative_audit_reference(path: Path) -> str:
    parts = path.parts
    try:
        start = [part.lower() for part in parts].index("audit_outputs")
    except ValueError:
        return path.name
    return "/".join(parts[start:])


def _lineage_json(
    input_file: str,
    input_sha: str,
    input_sheet: str,
    input_row: int | str,
) -> str:
    return json.dumps(
        [
            {
                "input_file": input_file,
                "input_file_sha256": input_sha,
                "input_sheet": input_sheet,
                "input_excel_row": input_row,
                "input_column_or_columns": "*",
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [value.strip() for value in stripped.split("|")]


def _is_separator_row(values: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", value.strip()) for value in values)


def _normalized_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", normalized).strip("_")


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
