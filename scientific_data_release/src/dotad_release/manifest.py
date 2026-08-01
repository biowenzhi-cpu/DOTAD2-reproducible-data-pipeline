from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .models import BuildContext, ExportResult
from .workbook_reader import sha256_file, write_tsv


CORE_COUNT_FIELDS = (
    "metric_name",
    "metric_value",
    "table_name",
    "row_count",
    "row_definition",
)
KEY_INTEGRITY_FIELDS = (
    "table_name",
    "row_count",
    "nonblank_record_ids",
    "unique_record_ids",
    "duplicate_record_ids",
    "blank_record_ids",
    "status",
)
MANIFEST_FIELDS = (
    "file_path",
    "table_name",
    "row_count",
    "row_definition",
    "lineage_required",
    "sha256",
)


def write_core_qc(
    context: BuildContext,
    core_results: Iterable[ExportResult],
) -> tuple[ExportResult, ExportResult]:
    results = sorted(core_results, key=lambda result: result.table_name)
    by_table = {result.table_name: result for result in results}
    count_rows = []
    for result in results:
        metric_name = {
            "antibody_metadata": "metadata_record_count",
            "antibody_sequences": "sequence_record_count",
            "literature_curated_records": "literature_curated_record_count",
        }.get(result.table_name, f"{result.table_name}_record_count")
        count_rows.append(
            _count_row(
                metric_name,
                len(result.rows),
                result.table_name,
                result.row_definition,
            )
        )
    count_rows.extend(_metadata_count_rows(by_table.get("antibody_metadata")))
    count_rows.extend(
        _sequence_count_rows(
            by_table.get("antibody_sequences"),
            by_table.get("sequence_qc"),
            by_table.get("sequence_fingerprints"),
        )
    )
    count_rows.extend(_experimental_count_rows(results))
    count_rows.sort(key=lambda row: (row["metric_name"], row["table_name"]))
    integrity_rows: list[dict[str, Any]] = []
    for result in results:
        ids = [str(row.get("record_id", "")) for row in result.rows]
        nonblank = [value for value in ids if value]
        unique = set(nonblank)
        duplicate_count = len(nonblank) - len(unique)
        blank_count = len(ids) - len(nonblank)
        integrity_rows.append(
            {
                "table_name": result.table_name,
                "row_count": len(ids),
                "nonblank_record_ids": len(nonblank),
                "unique_record_ids": len(unique),
                "duplicate_record_ids": duplicate_count,
                "blank_record_ids": blank_count,
                "status": (
                    "PASS"
                    if duplicate_count == 0 and blank_count == 0
                    else "FAIL"
                ),
            }
        )
    count_path = context.output_dir / "qc" / "core_table_counts.tsv"
    integrity_path = context.output_dir / "qc" / "key_integrity.tsv"
    write_tsv(count_path, count_rows, CORE_COUNT_FIELDS)
    write_tsv(integrity_path, integrity_rows, KEY_INTEGRITY_FIELDS)
    return (
        ExportResult(
            "core_table_counts",
            count_path,
            CORE_COUNT_FIELDS,
            tuple(count_rows),
            (
                "one row per explicit table-level or record-definition metric; "
                "companion tables and undefined grand totals are excluded"
            ),
        ),
        ExportResult(
            "key_integrity",
            integrity_path,
            KEY_INTEGRITY_FIELDS,
            tuple(integrity_rows),
            "one row per checked core data table",
        ),
    )


def _count_row(
    metric_name: str,
    value: int,
    table_name: str,
    row_definition: str,
) -> dict[str, Any]:
    return {
        "metric_name": metric_name,
        "metric_value": value,
        "table_name": table_name,
        "row_count": value,
        "row_definition": row_definition,
    }


def _metadata_count_rows(
    result: ExportResult | None,
) -> list[dict[str, Any]]:
    if result is None:
        return []
    identifiers = [
        str(row.get("antibody_id", "")).strip()
        for row in result.rows
        if str(row.get("antibody_id", "")).strip()
    ]
    unique = len(set(identifiers))
    return [
        _count_row(
            "non_missing_antibody_id_count",
            len(identifiers),
            result.table_name,
            "metadata source rows with a non-missing antibody_id",
        ),
        _count_row(
            "unique_antibody_id_count",
            unique,
            result.table_name,
            "distinct non-missing antibody_id values in metadata source rows",
        ),
        _count_row(
            "duplicate_antibody_id_count",
            len(identifiers) - unique,
            result.table_name,
            (
                "metadata source-row occurrences beyond the first occurrence "
                "of each non-missing antibody_id"
            ),
        ),
    ]


def _sequence_count_rows(
    sequences: ExportResult | None,
    qc: ExportResult | None,
    fingerprints: ExportResult | None,
) -> list[dict[str, Any]]:
    if sequences is None:
        return []
    classes = [
        str(row.get("sequence_class", ""))
        for row in sequences.rows
    ]
    qc_rows = qc.rows if qc is not None else ()
    metrics = {
        "records_with_vh": sum(
            _truthy(row.get("vh_available")) for row in qc_rows
        ),
        "records_with_vl": sum(
            _truthy(row.get("vl_available")) for row in qc_rows
        ),
        "records_with_paired_vh_vl": classes.count("paired_vh_vl"),
        "records_with_heavy_only": classes.count("heavy_only"),
        "records_with_light_only": classes.count("light_only"),
    }
    if not qc_rows:
        metrics["records_with_vh"] = sum(
            value in {"paired_vh_vl", "heavy_only"} for value in classes
        )
        metrics["records_with_vl"] = sum(
            value in {"paired_vh_vl", "light_only"} for value in classes
        )
    if fingerprints is not None:
        metrics.update(
            {
                "unique_normalized_vh": _unique_nonblank(
                    fingerprints.rows, "normalized_vh_sha256"
                ),
                "unique_normalized_vl": _unique_nonblank(
                    fingerprints.rows, "normalized_vl_sha256"
                ),
                "unique_normalized_paired_vh_vl": _unique_nonblank(
                    fingerprints.rows, "normalized_vh_vl_pair_sha256"
                ),
            }
        )
    return [
        _count_row(
            name,
            value,
            sequences.table_name,
            f"sequence records satisfying {name}",
        )
        for name, value in sorted(metrics.items())
    ]


def _experimental_count_rows(
    results: Sequence[ExportResult],
) -> list[dict[str, Any]]:
    raw = sum(
        len(result.rows)
        for result in results
        if result.table_name.endswith("_raw_measurements")
    )
    summary = sum(
        len(result.rows)
        for result in results
        if result.table_name.endswith("_summary_measurements")
    )
    rows = []
    if raw:
        rows.append(
            _count_row(
                "experimental_raw_record_count",
                raw,
                "experimental_measurements",
                "non-empty source rows across the three raw GDPa block tables",
            )
        )
    if summary:
        rows.append(
            _count_row(
                "experimental_summary_record_count",
                summary,
                "experimental_measurements",
                (
                    "non-empty source rows across the three summary GDPa block "
                    "tables; not added to raw rows as independent observations"
                ),
            )
        )
    return rows


def _unique_nonblank(
    rows: Sequence[dict[str, Any]],
    field: str,
) -> int:
    return len(
        {
            str(row.get(field, "")).strip()
            for row in rows
            if str(row.get(field, "")).strip()
        }
    )


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def write_release_manifest(
    context: BuildContext,
    lineage_required: Sequence[ExportResult],
    administrative: Sequence[ExportResult],
) -> Path:
    rows: list[dict[str, Any]] = []
    required_ids = {id(result) for result in lineage_required}
    for result in sorted(
        [*lineage_required, *administrative],
        key=lambda value: value.path.relative_to(context.output_dir).as_posix(),
    ):
        rows.append(
            {
                "file_path": result.path.relative_to(
                    context.output_dir
                ).as_posix(),
                "table_name": result.table_name,
                "row_count": len(result.rows),
                "row_definition": result.row_definition,
                "lineage_required": (
                    "true" if id(result) in required_ids else "false"
                ),
                "sha256": sha256_file(result.path),
            }
        )
    path = context.output_dir / "release_manifest.tsv"
    write_tsv(path, rows, MANIFEST_FIELDS)
    return path


def write_checksums(release_root: Path) -> Path:
    root = Path(release_root)
    output = root / "checksums_sha256.txt"
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.resolve() != output.resolve()
        ),
        key=lambda value: value.relative_to(root).as_posix(),
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in paths
    ]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return output
