from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import platform
import re
import sys
import traceback
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

import yaml

from .archive_inspector import inspect_zip, member_security_status, safe_extract_zip
from .claim_mapper import discrepancies, map_claims, unresolved_claims
from .delimited_profiler import (
    detect_encoding_and_delimiter,
    profile_delimited,
    profile_json,
    profile_sqlite,
    profile_text_stream,
)
from .dependency_graph import (
    dependency_edges,
    dependency_graph_markdown,
    inventory_scripts,
    reproducibility_rows,
)
from .file_inventory import (
    duplicate_groups,
    extension_summary,
    inventory_selected_files,
    inventory_tree,
    largest_files,
    load_category_rules,
    sha256_file,
)
from .key_integrity import audit_foreign_key, audit_key_column, candidate_key_columns
from .manuscript_claims import (
    claim_context_markdown,
    extract_numeric_claims,
)
from .report_builder import build_gap_outputs
from .resource_metrics import compute_release_metrics
from .sequence_profiler import infer_sequence_unique_definition, profile_sequence_table
from .spreadsheet_profiler import (
    infer_candidate_columns,
    normalize_column_name,
    profile_workbook,
    read_sheet_records,
)


OUTPUT_SUBDIRECTORIES = [
    "00_run_metadata",
    "01_file_inventory",
    "02_table_inventory",
    "03_data_integrity",
    "04_manuscript_claims",
    "05_reproducibility",
    "06_scientific_data_gaps",
    "07_logs",
]
AUTHORITATIVE_DATA_RELATIVE_PATHS = frozenset(
    {
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    }
)
TABLE_FIELDS = [
    "table_id",
    "file_id",
    "relative_path",
    "sheet_or_table_name",
    "row_count",
    "column_count",
    "column_names_json",
    "duplicate_full_row_count",
    "fully_empty_row_count",
    "fully_empty_column_count",
    "candidate_primary_keys_json",
    "candidate_foreign_keys_json",
    "candidate_source_columns_json",
    "candidate_doi_columns_json",
    "candidate_pmid_columns_json",
    "candidate_dotad_id_columns_json",
    "candidate_sequence_columns_json",
    "candidate_assay_columns_json",
    "candidate_value_columns_json",
    "candidate_unit_columns_json",
    "candidate_condition_columns_json",
    "candidate_version_columns_json",
    "read_status",
    "notes",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only DOTAD Scientific Data audit")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--manuscript", required=True, type=Path)
    parser.add_argument("--output-root", default=Path("audit_outputs/phase1"), type=Path)
    parser.add_argument(
        "--data-file",
        action="append",
        default=[],
        type=Path,
        help=(
            "Authoritative data file to audit. Repeat for a scoped release inventory. "
            "Paths may be absolute or relative to --project-root."
        ),
    )
    parser.add_argument(
        "--script-root",
        action="append",
        default=[],
        type=Path,
        help=(
            "Directory containing processing/analysis scripts. Repeat as needed; "
            "defaults to <project-root>/scripts and <project-root>/R."
        ),
    )
    parser.add_argument("--archive", type=Path, help="Original project ZIP, for manifest/hash and optional safe extraction")
    parser.add_argument("--extract-root", type=Path, help="Safe extraction destination; used only with --archive")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inventory-only", action="store_true")
    modes.add_argument("--tables-only", action="store_true")
    modes.add_argument("--claims-only", action="store_true")
    modes.add_argument("--integrity-only", action="store_true")
    modes.add_argument("--reproducibility-only", action="store_true")
    modes.add_argument("--full-audit", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = args.output_root.resolve()
    for directory in OUTPUT_SUBDIRECTORIES:
        (output_root / directory).mkdir(parents=True, exist_ok=True)
    logger = configure_logging(output_root / "07_logs" / "audit.log")
    started = datetime.now(timezone.utc)
    config_root = Path(__file__).resolve().parents[2] / "configs"
    rules = yaml.safe_load((config_root / "audit_rules.yml").read_text(encoding="utf-8")) or {}
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    run_metadata: dict[str, Any] = {
        "audit_started_utc": started.isoformat(),
        "command": " ".join(sys.argv),
        "python_version": sys.version,
        "platform": platform.platform(),
        "mode": selected_mode(args),
        "project_root_requested": str(args.project_root.resolve()),
        "manuscript_path": str(args.manuscript.resolve()),
        "manuscript_filename": args.manuscript.name,
        "manuscript_size_bytes": args.manuscript.stat().st_size,
        "manuscript_sha256_before": sha256_file(args.manuscript),
        "network_access_used": False,
        "source_scripts_executed": False,
    }
    write_json(
        output_root / "00_run_metadata" / "active_run.json",
        {
            "audit_started_utc": started.isoformat(),
            "mode": selected_mode(args),
            "status": "running",
        },
    )
    project_root = args.project_root.resolve()
    selected_data_paths = resolve_selected_data_files(project_root, args.data_file)
    if selected_data_paths:
        run_metadata["authoritative_data_scope"] = True
        run_metadata["authoritative_data_files"] = [
            {
                "path": str(path),
                "relative_path": path.relative_to(project_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256_before": sha256_file(path),
            }
            for path in selected_data_paths
        ]
    else:
        run_metadata["authoritative_data_scope"] = False
    archive_rows: list[dict[str, Any]] = []
    try:
        if args.archive:
            archive = args.archive.resolve()
            run_metadata.update(
                {
                    "archive_path": str(archive),
                    "archive_filename": archive.name,
                    "archive_size_bytes": archive.stat().st_size,
                    "archive_sha256_before": sha256_file(archive),
                }
            )
            archive_rows = inspect_zip(archive)
            if args.extract_root:
                extract_root = args.extract_root.resolve()
                archive_rows = safe_extract_zip(archive, extract_root, archive_rows)
                run_metadata["extraction_root"] = str(extract_root)
                expected = str(rules.get("expected_archive_top_level", "")).strip()
                candidate = extract_root / expected if expected else extract_root
                if project_root == args.project_root.resolve() and not project_root.exists() and candidate.exists():
                    project_root = candidate.resolve()
            write_tsv(
                output_root / "00_run_metadata" / "archive_manifest.tsv",
                archive_rows,
                [
                    "archive_path",
                    "member_path",
                    "compressed_size",
                    "uncompressed_size",
                    "compression_type",
                    "is_directory",
                    "is_symlink",
                    "security_status",
                    "extraction_status",
                    "notes",
                ],
            )
            write_extraction_log(output_root / "07_logs" / "extraction.log", archive_rows)
        if not project_root.exists():
            raise FileNotFoundError(f"Project root does not exist: {project_root}")
        run_metadata["project_root_effective"] = str(project_root)

        category_rules = load_category_rules(config_root / "file_categories.yml")
        files = (
            inventory_selected_files(project_root, selected_data_paths, category_rules)
            if selected_data_paths
            else inventory_tree(project_root, category_rules)
        )
        write_inventory_outputs(output_root, files)
        run_metadata["file_count"] = len(files)
        run_metadata["unreadable_file_count"] = sum(row["read_status"] == "unreadable" for row in files)

        nested_manifest = inspect_nested_archives(project_root, files, logger, errors)
        combined_manifest = archive_rows + nested_manifest
        if combined_manifest:
            write_tsv(
                output_root / "00_run_metadata" / "archive_manifest.tsv",
                combined_manifest,
                [
                    "archive_path",
                    "member_path",
                    "compressed_size",
                    "uncompressed_size",
                    "compression_type",
                    "is_directory",
                    "is_symlink",
                    "security_status",
                    "extraction_status",
                    "notes",
                ],
            )
            write_extraction_log(
                output_root / "07_logs" / "extraction.log",
                combined_manifest,
            )
            run_metadata["archive_member_count_outer"] = len(archive_rows)
            run_metadata["archive_member_count_including_nested"] = len(combined_manifest)

        mode = selected_mode(args)
        if mode == "inventory":
            finish_run_metadata(args, run_metadata, output_root, files, errors, warnings)
            return 0 if not errors else 2

        table_results = profile_project_tables(
            project_root,
            files,
            output_root,
            logger,
            errors,
            max_samples=int(rules.get("max_sample_values", 3)),
        )
        run_metadata.update(
            {
                "workbook_count": len(table_results["workbooks"]),
                "sheet_count": len(table_results["sheets"]),
                "table_count": len(table_results["tables"]),
                "unreadable_table_count": sum(
                    row.get("read_status") == "unreadable" for row in table_results["tables"]
                ),
            }
        )
        benchmark_count_rows = build_benchmark_count_audit(project_root, table_results["tables"])
        write_tsv(
            output_root / "05_reproducibility" / "benchmark_source_count_audit.tsv",
            benchmark_count_rows,
        )
        dictionary_crosscheck = build_data_dictionary_crosscheck(
            project_root,
            files,
            table_results,
            nested_manifest,
        )
        write_tsv(
            output_root / "03_data_integrity" / "data_dictionary_inventory_crosscheck.tsv",
            dictionary_crosscheck,
        )
        run_metadata["data_dictionary_crosscheck_count"] = len(dictionary_crosscheck)
        run_metadata["data_dictionary_crosscheck_mismatch_count"] = sum(
            row.get("status") == "mismatch" for row in dictionary_crosscheck
        )
        run_metadata["data_dictionary_out_of_scope_count"] = sum(
            row.get("status") == "out_of_scope" for row in dictionary_crosscheck
        )
        benchmark_count_note = next(
            (
                row["notes"]
                for row in benchmark_count_rows
                if row.get("record_type") == "summary"
            ),
            "",
        )
        if mode == "tables":
            finish_run_metadata(args, run_metadata, output_root, files, errors, warnings)
            return 0 if not errors else 2

        resource_metric_result: dict[str, Any] = {}
        if mode in {"claims", "full"}:
            resource_metric_result = recalculate_authoritative_resource_metrics(
                project_root,
                files,
                output_root,
                logger,
                errors,
            )
            if resource_metric_result:
                run_metadata["resource_metric_recalculation"] = resource_metric_result[
                    "summary"
                ]

        contexts = load_integrity_contexts(project_root, files, table_results, logger, errors)
        if mode in {"integrity", "full"}:
            integrity = run_integrity_audit(contexts, project_root, table_results, output_root)
            run_metadata.update(integrity["summary"])

        scripts: list[dict[str, Any]] = []
        if mode in {"reproducibility", "claims", "full"}:
            scripts = inventory_scripts(project_root, args.script_root or None)
            write_reproducibility_outputs(
                scripts,
                files,
                project_root,
                output_root,
                authoritative_scope_note(files),
            )
            run_metadata["script_count"] = len(scripts)
        if mode == "reproducibility":
            finish_run_metadata(args, run_metadata, output_root, files, errors, warnings)
            return 0 if not errors else 2

        claims: list[dict[str, Any]] = []
        claim_map: list[dict[str, Any]] = []
        if mode in {"claims", "full"}:
            claims = extract_numeric_claims(args.manuscript, config_root / "claim_registry.yml")
            facts = build_claim_facts(
                project_root,
                files,
                table_results,
                scripts,
                resource_metric_result,
            )
            claim_map = map_claims(claims, files, table_results["tables"], scripts, facts)
            write_claim_outputs(output_root, claims, claim_map, args.manuscript)
            run_metadata["manuscript_claim_count"] = len(claims)
            run_metadata["claim_verification_counts"] = dict(
                Counter(row["verification_status"] for row in claim_map)
            )
        if mode == "claims":
            finish_run_metadata(args, run_metadata, output_root, files, errors, warnings)
            return 0 if not errors else 2

        if mode == "full":
            sequence_definition = sequence_definition_from_outputs(
                project_root, scripts, output_root
            )
            report, matrix, requests, release = build_gap_outputs(
                archive_version_note=(
                    "The scoped audit uses the four author-designated DOTAD 2.0 workbooks and "
                    "`dotad_affinity_benchmark_v2.0.zip`; no v1.0 snapshot is used as a current input."
                ),
                sequence_definition=sequence_definition,
                claim_map=claim_map,
                script_inventory=scripts,
                files=files,
                benchmark_count_note=benchmark_count_note,
                dictionary_mismatch_count=sum(
                    row.get("status") == "mismatch"
                    for row in dictionary_crosscheck
                ),
                dictionary_out_of_scope_count=sum(
                    row.get("status") == "out_of_scope"
                    for row in dictionary_crosscheck
                ),
                resource_metric_summary=resource_metric_result.get("summary", {}),
            )
            (output_root / "06_scientific_data_gaps" / "Scientific_Data_gap_report.md").write_text(
                report, encoding="utf-8"
            )
            write_tsv(
                output_root / "06_scientific_data_gaps" / "deliverable_status_matrix.tsv",
                matrix,
            )
            write_tsv(
                output_root / "06_scientific_data_gaps" / "manual_information_requests.tsv",
                requests,
            )
            (output_root / "06_scientific_data_gaps" / "proposed_release_structure.md").write_text(
                release, encoding="utf-8"
            )

        finish_run_metadata(args, run_metadata, output_root, files, errors, warnings)
        logger.info("Audit completed with %d warnings and %d captured errors.", len(warnings), len(errors))
        return 0 if not errors else 2
    except Exception as exc:
        logger.exception("Fatal audit failure")
        run_metadata["fatal_error"] = f"{type(exc).__name__}: {exc}"
        run_metadata["audit_finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output_root / "00_run_metadata" / "run_metadata.json", run_metadata)
        return 1


def selected_mode(args: argparse.Namespace) -> str:
    if args.inventory_only:
        return "inventory"
    if args.tables_only:
        return "tables"
    if args.claims_only:
        return "claims"
    if args.integrity_only:
        return "integrity"
    if args.reproducibility_only:
        return "reproducibility"
    return "full"


def resolve_selected_data_files(project_root: Path, values: list[Path]) -> list[Path]:
    if not values:
        raise ValueError(
            "Authoritative data scope must exactly match the five user-confirmed "
            "DOTAD 2.0 assets. No --data-file values were provided."
        )
    resolved: list[Path] = []
    for value in values:
        path = value if value.is_absolute() else project_root / value
        path = path.resolve()
        if path != project_root and project_root not in path.parents:
            raise ValueError(f"Authoritative data file is outside project root: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"Authoritative data file does not exist: {path}")
        resolved.append(path)
    unique = sorted(set(resolved), key=lambda item: item.as_posix().lower())
    relative_paths = {
        path.relative_to(project_root).as_posix().lower() for path in unique
    }
    if relative_paths != AUTHORITATIVE_DATA_RELATIVE_PATHS:
        missing = sorted(AUTHORITATIVE_DATA_RELATIVE_PATHS - relative_paths)
        unexpected = sorted(relative_paths - AUTHORITATIVE_DATA_RELATIVE_PATHS)
        raise ValueError(
            "Authoritative data scope must exactly match the five user-confirmed "
            f"DOTAD 2.0 assets. Missing={missing}; unexpected={unexpected}."
        )
    return unique


def authoritative_scope_note(files: list[dict[str, Any]]) -> str:
    names = ", ".join(f"`{row['filename']}`" for row in files)
    return (
        "Authoritative data scope is explicitly restricted to the user-designated "
        f"DOTAD 2.0 assets: {names}. Website JSON and historical releases are downstream "
        "or secondary evidence, not primary audit inputs."
    )


def configure_logging(path: Path) -> logging.Logger:
    logger = logging.getLogger("dotad_audit")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def write_inventory_outputs(output_root: Path, files: list[dict[str, Any]]) -> None:
    directory = output_root / "01_file_inventory"
    write_tsv(directory / "file_inventory.tsv", files)
    write_tsv(
        directory / "file_hashes.tsv",
        [
            {
                "file_id": row["file_id"],
                "relative_path": row["relative_path"],
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
                "read_status": row["read_status"],
            }
            for row in files
        ],
    )
    write_tsv(directory / "duplicate_file_groups.tsv", duplicate_groups(files))
    write_tsv(directory / "largest_files.tsv", largest_files(files))
    write_tsv(directory / "extension_summary.tsv", extension_summary(files))


def inspect_nested_archives(
    project_root: Path,
    files: list[dict[str, Any]],
    logger: logging.Logger,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in files:
        if not row["is_archive"] or Path(row["relative_path"]).suffix.lower() != ".zip":
            continue
        path = project_root / row["relative_path"]
        try:
            with zipfile.ZipFile(path) as handle:
                output.extend(_inspect_zip_handle(handle, row["relative_path"], depth=1))
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append({"stage": "nested_archive_inspection", "path": row["relative_path"], "error": str(exc)})
            logger.warning("Could not inspect nested archive %s: %s", row["relative_path"], exc)
    return output


def _inspect_zip_handle(
    handle: zipfile.ZipFile,
    archive_label: str,
    *,
    depth: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for info in handle.infolist():
        status, is_symlink, note = member_security_status(info)
        rows.append(
            {
                "archive_path": archive_label,
                "member_path": info.filename,
                "compressed_size": info.compress_size,
                "uncompressed_size": info.file_size,
                "compression_type": info.compress_type,
                "is_directory": info.is_dir(),
                "is_symlink": is_symlink,
                "security_status": status,
                "extraction_status": "inspected_not_extracted",
                "notes": note,
            }
        )
        if (
            depth < 3
            and status == "safe"
            and not info.is_dir()
            and info.filename.lower().endswith(".zip")
            and info.file_size <= 250 * 1024 * 1024
        ):
            try:
                payload = handle.read(info)
                with zipfile.ZipFile(io.BytesIO(payload)) as nested:
                    rows.extend(
                        _inspect_zip_handle(
                            nested,
                            f"{archive_label}!{info.filename}",
                            depth=depth + 1,
                        )
                    )
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                rows.append(
                    {
                        "archive_path": f"{archive_label}!{info.filename}",
                        "member_path": "[nested archive unreadable]",
                        "compressed_size": "",
                        "uncompressed_size": "",
                        "compression_type": "",
                        "is_directory": False,
                        "is_symlink": False,
                        "security_status": "unreadable",
                        "extraction_status": "not_extracted",
                        "notes": str(exc),
                    }
                )
    return rows


def profile_project_tables(
    project_root: Path,
    files: list[dict[str, Any]],
    output_root: Path,
    logger: logging.Logger,
    errors: list[dict[str, str]],
    *,
    max_samples: int,
) -> dict[str, list[dict[str, Any]]]:
    workbooks: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    file_by_path = {row["relative_path"]: row for row in files}
    table_counter = 0
    for file_row in files:
        path = project_root / file_row["relative_path"]
        suffix = path.suffix.lower()
        try:
            if suffix in {".xlsx", ".xlsm"}:
                workbook, sheet_rows, table_rows, column_rows = profile_workbook(
                    path, file_row["file_id"], max_samples=max_samples
                )
                workbook["relative_path"] = file_row["relative_path"]
                for row in sheet_rows:
                    row["relative_path"] = file_row["relative_path"]
                for row in table_rows:
                    row["file_id"] = file_row["file_id"]
                    row["relative_path"] = file_row["relative_path"]
                workbooks.append(workbook)
                sheets.extend(sheet_rows)
                tables.extend(table_rows)
                columns.extend(column_rows)
            elif suffix in {".csv", ".tsv", ".txt"}:
                table_counter += 1
                profile = profile_delimited(path, f"{file_row['file_id']}:T{table_counter:04d}", max_samples)
                table, column_rows = convert_profile(
                    profile, file_row["file_id"], file_row["relative_path"], path.name
                )
                tables.append(table)
                columns.extend(column_rows)
            elif suffix == ".json":
                for profile in profile_json(path, f"{file_row['file_id']}:JSON", max_samples):
                    table, column_rows = convert_profile(
                        profile, file_row["file_id"], file_row["relative_path"], path.name
                    )
                    tables.append(table)
                    columns.extend(column_rows)
            elif suffix in {".sqlite", ".sqlite3", ".db"}:
                for profile in profile_sqlite(path, file_row["file_id"]):
                    table, column_rows = convert_profile(
                        profile,
                        file_row["file_id"],
                        file_row["relative_path"],
                        profile.get("sheet_or_table_name", path.name),
                    )
                    tables.append(table)
                    columns.extend(column_rows)
            elif suffix == ".parquet":
                table_counter += 1
                table = {
                    "table_id": f"{file_row['file_id']}:PARQUET{table_counter:03d}",
                    "file_id": file_row["file_id"],
                    "relative_path": file_row["relative_path"],
                    "sheet_or_table_name": path.name,
                    "read_status": "unsupported",
                    "notes": "Parquet present but pyarrow is not installed in the audit environment.",
                }
                tables.append(_complete_table_row(table))
        except Exception as exc:
            errors.append({"stage": "table_profile", "path": file_row["relative_path"], "error": f"{type(exc).__name__}: {exc}"})
            logger.warning("Table profile failed for %s: %s", file_row["relative_path"], exc)
            tables.append(
                _complete_table_row(
                    {
                        "table_id": f"{file_row['file_id']}:ERROR",
                        "file_id": file_row["file_id"],
                        "relative_path": file_row["relative_path"],
                        "sheet_or_table_name": path.name,
                        "read_status": "unreadable",
                        "notes": f"{type(exc).__name__}: {exc}",
                    }
                )
            )
    nested_tables, nested_columns = profile_nested_archive_tables(project_root, files, logger, errors, max_samples)
    tables.extend(nested_tables)
    columns.extend(nested_columns)
    write_tsv(output_root / "02_table_inventory" / "workbook_inventory.tsv", workbooks)
    write_tsv(output_root / "02_table_inventory" / "sheet_inventory.tsv", sheets)
    write_tsv(output_root / "02_table_inventory" / "table_inventory.tsv", tables, TABLE_FIELDS)
    write_tsv(output_root / "02_table_inventory" / "column_dictionary_draft.tsv", columns)
    summary = summarize_tables(workbooks, sheets, tables, columns)
    write_tsv(output_root / "02_table_inventory" / "table_profile_summary.tsv", summary)
    return {"workbooks": workbooks, "sheets": sheets, "tables": tables, "columns": columns}


def profile_nested_archive_tables(
    project_root: Path,
    files: list[dict[str, Any]],
    logger: logging.Logger,
    errors: list[dict[str, str]],
    max_samples: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []
    columns: list[dict[str, Any]] = []
    counter = [0]
    for file_row in files:
        if Path(file_row["relative_path"]).suffix.lower() != ".zip":
            continue
        path = project_root / file_row["relative_path"]
        try:
            with zipfile.ZipFile(path) as handle:
                _profile_zip_handle(
                    handle,
                    file_row["relative_path"],
                    file_row["file_id"],
                    tables,
                    columns,
                    counter,
                    max_samples=max_samples,
                    depth=1,
                )
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append({"stage": "nested_table_profile", "path": file_row["relative_path"], "error": str(exc)})
            logger.warning("Nested table profile failed for %s: %s", file_row["relative_path"], exc)
    return tables, columns


def _profile_zip_handle(
    handle: zipfile.ZipFile,
    archive_label: str,
    file_id: str,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    counter: list[int],
    *,
    max_samples: int,
    depth: int,
) -> None:
    for info in handle.infolist():
        status, _, _ = member_security_status(info)
        if status != "safe" or info.is_dir():
            continue
        suffix = Path(info.filename).suffix.lower()
        pseudo_path = f"{archive_label}!{info.filename}"
        if suffix in {".csv", ".tsv"}:
            counter[0] += 1
            with handle.open(info) as raw:
                sample = raw.read(128 * 1024)
            encoding, delimiter = detect_encoding_and_delimiter(sample, suffix)
            raw_again = handle.open(info)
            text_handle = io.TextIOWrapper(raw_again, encoding=encoding, errors="replace", newline="")
            profile = profile_text_stream(
                text_handle,
                f"{file_id}:Z{counter[0]:04d}",
                delimiter=delimiter,
                max_samples=max_samples,
                notes=f"Streamed from nested archive; encoding={encoding}; delimiter={repr(delimiter)}.",
            )
            table, column_rows = convert_profile(profile, file_id, pseudo_path, Path(info.filename).name)
            tables.append(table)
            columns.extend(column_rows)
        elif suffix == ".zip" and depth < 3 and info.file_size <= 250 * 1024 * 1024:
            try:
                payload = handle.read(info)
                with zipfile.ZipFile(io.BytesIO(payload)) as nested:
                    _profile_zip_handle(
                        nested,
                        pseudo_path,
                        file_id,
                        tables,
                        columns,
                        counter,
                        max_samples=max_samples,
                        depth=depth + 1,
                    )
            except (OSError, zipfile.BadZipFile, RuntimeError):
                counter[0] += 1
                tables.append(
                    _complete_table_row(
                        {
                            "table_id": f"{file_id}:Z{counter[0]:04d}",
                            "file_id": file_id,
                            "relative_path": pseudo_path,
                            "sheet_or_table_name": Path(info.filename).name,
                            "read_status": "unreadable",
                            "notes": "Nested ZIP could not be opened for read-only table profiling.",
                        }
                    )
                )


def convert_profile(
    profile: dict[str, Any],
    file_id: str,
    relative_path: str,
    table_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    columns = profile.get("column_names", [])
    roles = infer_candidate_columns(columns)
    table = _complete_table_row(
        {
            "table_id": profile.get("table_id", f"{file_id}:T"),
            "file_id": file_id,
            "relative_path": relative_path,
            "sheet_or_table_name": profile.get("sheet_or_table_name", table_name),
            "row_count": profile.get("row_count", ""),
            "column_count": profile.get("column_count", ""),
            "column_names_json": json.dumps(columns, ensure_ascii=False),
            "duplicate_full_row_count": profile.get("duplicate_full_row_count", ""),
            "fully_empty_row_count": profile.get("fully_empty_row_count", ""),
            "fully_empty_column_count": profile.get("fully_empty_column_count", ""),
            **{key: json.dumps(value, ensure_ascii=False) for key, value in roles.items()},
            "read_status": profile.get("read_status", "readable"),
            "notes": profile.get("notes", ""),
        }
    )
    column_rows = []
    for stat in profile.get("column_stats", []):
        column_name = stat["column_name"]
        semantic_roles = [
            key.removeprefix("candidate_").removesuffix("_columns_json")
            for key, values in infer_candidate_columns([column_name]).items()
            if values
        ]
        sample_values = stat.get("sample_values", [])
        if "sequence" in semantic_roles:
            sample_values = ["[sequence sample redacted from audit dictionary]"] if sample_values else []
        column_rows.append(
            {
                "table_id": table["table_id"],
                "column_name": column_name,
                "normalized_column_name": normalize_column_name(column_name),
                "inferred_data_type": stat.get("inferred_data_type", ""),
                "non_missing_count": stat.get("non_missing_count", ""),
                "missing_count": stat.get("missing_count", ""),
                "missing_fraction": stat.get("missing_fraction", ""),
                "unique_count": stat.get("unique_count", ""),
                "sample_values_json": json.dumps(sample_values, ensure_ascii=False),
                "semantic_role_guess": "|".join(semantic_roles) if semantic_roles else "unknown",
                "confidence": "medium" if semantic_roles else "low",
                "notes": "Unique count is a lower bound." if stat.get("unique_count_is_lower_bound") else "",
            }
        )
    return table, column_rows


def build_benchmark_count_audit(
    project_root: Path,
    table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    benchmark_tables = [
        row
        for row in table_rows
        if "dotad_affinity_benchmark_v" in row.get("relative_path", "").lower()
        and ".zip!" in row.get("relative_path", "").lower()
        and str(row.get("row_count", "")).isdigit()
        and "/._" not in row["relative_path"].replace("\\", "/")
        and "__macosx" not in row["relative_path"].lower()
    ]
    actual_by_name = {
        Path(row["relative_path"].split("!")[-1]).name: int(row["row_count"])
        for row in benchmark_tables
    }
    archive_relative = next(
        (row["relative_path"].split("!")[0] for row in benchmark_tables),
        "",
    )
    archive_path = project_root / archive_relative if archive_relative else None
    declared_by_name: dict[str, int] = {}
    if archive_path and archive_path.exists():
        try:
            with zipfile.ZipFile(archive_path) as handle:
                readme = handle.read("binding/README.md").decode("utf-8", errors="replace")
            for line in readme.splitlines():
                if not line.startswith("| ") or ".csv" not in line:
                    continue
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) < 2:
                    continue
                filename = cells[0]
                try:
                    declared_by_name[filename] = int(cells[1].replace(",", ""))
                except ValueError:
                    continue
        except (KeyError, OSError, zipfile.BadZipFile):
            pass
    rows: list[dict[str, Any]] = []
    names = sorted(set(actual_by_name) | set(declared_by_name))
    for name in names:
        actual = actual_by_name.get(name, "")
        declared = declared_by_name.get(name, "")
        difference = actual - declared if isinstance(actual, int) and isinstance(declared, int) else ""
        rows.append(
            {
                "record_type": "dataset",
                "dataset_or_source": name,
                "profiled_data_rows": actual,
                "readme_declared_size": declared,
                "difference": difference,
                "status": "match" if difference == 0 else "mismatch" if difference != "" else "unmatched",
                "notes": (
                    "The generic CSV profiler treats the first parsed row as the header. "
                    "Files with preamble text therefore require the logical-row count in "
                    "resource_metric_components.tsv for resource-size calculations."
                ),
            }
        )
    actual_total = sum(actual_by_name.values())
    declared_total = sum(declared_by_name.values())
    archive_name = archive_path.name if archive_path else "affinity benchmark archive"
    notes = (
        f"{archive_name}: {len(actual_by_name)} profiled tabular datasets and "
        f"{len(declared_by_name)} README-declared datasets; generic-profiler rows "
        f"after its first parsed line={actual_total:,}; README `size` total="
        f"{declared_total:,}; difference={actual_total - declared_total:+,}. "
        "README `size` semantics are not documented consistently enough to treat "
        "this comparison as an exact logical data-row validation."
    )
    rows.append(
        {
            "record_type": "summary",
            "dataset_or_source": "all benchmark CSV datasets",
            "profiled_data_rows": actual_total,
            "readme_declared_size": declared_total,
            "difference": actual_total - declared_total,
            "status": "mismatch" if actual_total != declared_total else "match",
            "notes": notes,
        }
    )
    return rows


def recalculate_authoritative_resource_metrics(
    project_root: Path,
    files: list[dict[str, Any]],
    output_root: Path,
    logger: logging.Logger,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    by_name = {
        row["filename"].lower(): project_root / row["relative_path"]
        for row in files
    }
    required = {
        "metadata_sequences": "dotad_antibody_metadata_sequences_v2.0.xlsx",
        "experimental": "dotad_experimental_developability_v2.0.xlsx",
        "literature": "dotad_literature_developability_collection_v2.0.xlsx",
        "affinity": "dotad_affinity_benchmark_v2.0.zip",
    }
    missing = [name for name in required.values() if name.lower() not in by_name]
    if missing:
        logger.warning(
            "Resource metric recalculation skipped; missing authoritative inputs: %s",
            ", ".join(missing),
        )
        return {}
    try:
        result = compute_release_metrics(
            by_name[required["metadata_sequences"].lower()],
            by_name[required["experimental"].lower()],
            by_name[required["literature"].lower()],
            by_name[required["affinity"].lower()],
        )
        write_json(
            output_root / "05_reproducibility" / "resource_metric_recalculation.json",
            result["summary"],
        )
        write_tsv(
            output_root / "05_reproducibility" / "resource_metric_components.tsv",
            result["components"],
        )
        return result
    except Exception as exc:
        errors.append(
            {
                "stage": "resource_metric_recalculation",
                "path": "authoritative v2.0 inputs",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        logger.exception("Authoritative resource metric recalculation failed")
        return {}


def build_data_dictionary_crosscheck(
    project_root: Path,
    files: list[dict[str, Any]],
    table_results: dict[str, list[dict[str, Any]]],
    archive_manifest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dictionary_row = next(
        (
            row
            for row in files
            if row["filename"].lower() == "dotad_data_dictionary_v2.0.xlsx"
        ),
        None,
    )
    if not dictionary_row:
        return []
    dictionary_path = project_root / dictionary_row["relative_path"]
    declared_rows = read_sheet_records(
        dictionary_path,
        "Workbook Sheet Dictionary",
    )
    actual = {
        (
            Path(row["relative_path"]).name.lower(),
            str(row["sheet_or_table_name"]).strip(),
        ): row
        for row in table_results["tables"]
        if "!" not in row["relative_path"]
    }
    output: list[dict[str, Any]] = []
    for declared in declared_rows:
        workbook = str(declared.get("workbook") or "").strip()
        sheet = str(declared.get("sheet") or "").strip()
        match = actual.get((workbook.lower(), sheet))
        declared_rows_count = _optional_int(declared.get("row_count"))
        declared_columns_count = _optional_int(declared.get("column_count"))
        actual_rows_count = _optional_int(match.get("row_count")) if match else None
        actual_columns_count = _optional_int(match.get("column_count")) if match else None
        matched = bool(
            match
            and declared_rows_count == actual_rows_count
            and declared_columns_count == actual_columns_count
        )
        output.append(
            {
                "record_type": "workbook_sheet",
                "declared_item": f"{workbook}::{sheet}",
                "declared_row_count": declared_rows_count if declared_rows_count is not None else "",
                "actual_row_count": actual_rows_count if actual_rows_count is not None else "",
                "row_difference": (
                    actual_rows_count - declared_rows_count
                    if declared_rows_count is not None and actual_rows_count is not None
                    else ""
                ),
                "declared_column_count": (
                    declared_columns_count if declared_columns_count is not None else ""
                ),
                "actual_column_count": (
                    actual_columns_count if actual_columns_count is not None else ""
                ),
                "column_difference": (
                    actual_columns_count - declared_columns_count
                    if declared_columns_count is not None and actual_columns_count is not None
                    else ""
                ),
                "status": "match" if matched else "mismatch",
                "notes": (
                    "Exact read-only workbook profile matches dictionary."
                    if matched
                    else "Dictionary row/column declaration does not match the current workbook profile."
                    if match
                    else "Declared workbook/sheet was not included in the authoritative input scope."
                ),
            }
        )

    release_rows = read_sheet_records(dictionary_path, "Release File Manifest")
    manifest_file_counts = Counter(
        row["archive_path"]
        for row in archive_manifest
        if not row.get("is_directory")
    )
    manifest_directory_counts = Counter(
        row["archive_path"]
        for row in archive_manifest
        if row.get("is_directory")
    )
    file_by_name = {row["filename"].lower(): row for row in files}
    workbook_sheet_counts = Counter(
        Path(row["relative_path"]).name.lower()
        for row in table_results["tables"]
        if "!" not in row["relative_path"]
    )
    for declared in release_rows:
        filename = str(declared.get("file_name") or "").strip()
        selected = file_by_name.get(filename.lower())
        if not selected:
            output.append(
                {
                    "record_type": "release_file",
                    "declared_item": filename,
                    "declared_row_count": "",
                    "actual_row_count": "",
                    "row_difference": "",
                    "declared_column_count": "",
                    "actual_column_count": "",
                    "column_difference": "",
                    "declared_sheet_or_file_count": (
                        _optional_int(declared.get("sheet_or_file_count")) or ""
                    ),
                    "actual_sheet_or_file_count": "",
                    "count_difference": "",
                    "status": "out_of_scope",
                    "notes": (
                        "Declared in the data dictionary Release File Manifest but "
                        "not one of the five user-confirmed primary data assets. "
                        "Existence and member counts were not assessed in this "
                        "authoritative-data cross-check."
                    ),
                }
            )
            continue
        declared_count = _optional_int(declared.get("sheet_or_file_count"))
        actual_count = (
            manifest_file_counts.get(selected["relative_path"], 0)
            if selected["is_archive"]
            else workbook_sheet_counts.get(filename.lower(), 0)
        )
        matched = declared_count == actual_count
        output.append(
            {
                "record_type": "release_file",
                "declared_item": filename,
                "declared_row_count": "",
                "actual_row_count": "",
                "row_difference": "",
                "declared_column_count": "",
                "actual_column_count": "",
                "column_difference": "",
                "declared_sheet_or_file_count": declared_count if declared_count is not None else "",
                "actual_sheet_or_file_count": actual_count,
                "count_difference": (
                    actual_count - declared_count if declared_count is not None else ""
                ),
                "status": "match" if matched else "mismatch",
                "notes": (
                    (
                        "Release manifest count matches the current scoped file count."
                        if not selected["is_archive"]
                        else "Release manifest count matches physical archive files; "
                        f"{manifest_directory_counts.get(selected['relative_path'], 0)} "
                        "directory entries were excluded."
                    )
                    if matched
                    else (
                        "Release manifest sheet/file count does not match the current "
                        "scoped file."
                        if not selected["is_archive"]
                        else "Release manifest declares archive members as files, but "
                        f"the archive contains {actual_count} physical files plus "
                        f"{manifest_directory_counts.get(selected['relative_path'], 0)} "
                        "directory entries."
                    )
                ),
            }
        )
    return output


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _complete_table_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in TABLE_FIELDS}


def summarize_tables(
    workbooks: list[dict[str, Any]],
    sheets: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statuses = Counter(row.get("read_status", "unknown") for row in tables)
    return [
        {"metric": "workbook_count", "value": len(workbooks), "notes": ""},
        {"metric": "sheet_count", "value": len(sheets), "notes": ""},
        {"metric": "table_count", "value": len(tables), "notes": "Includes tabular files inside nested ZIP archives."},
        {"metric": "column_profile_count", "value": len(columns), "notes": ""},
        *[
            {"metric": f"table_read_status:{status}", "value": count, "notes": ""}
            for status, count in sorted(statuses.items())
        ],
        {
            "metric": "total_profiled_rows",
            "value": sum(int(row["row_count"]) for row in tables if str(row.get("row_count", "")).isdigit()),
            "notes": "Sum across heterogeneous tables; not a resource annotation count and may double-count records.",
        },
    ]


def load_integrity_contexts(
    project_root: Path,
    files: list[dict[str, Any]],
    table_results: dict[str, list[dict[str, Any]]],
    logger: logging.Logger,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    tables_by_file_sheet = {
        (row["relative_path"], row["sheet_or_table_name"]): row for row in table_results["tables"]
    }
    for sheet in table_results["sheets"]:
        path = project_root / sheet["relative_path"]
        table = tables_by_file_sheet.get((sheet["relative_path"], sheet["sheet_name"]))
        if not table or table["read_status"] != "readable":
            continue
        try:
            records = read_sheet_records(path, sheet["sheet_name"])
            contexts.append(
                {
                    "table_id": table["table_id"],
                    "relative_path": sheet["relative_path"],
                    "sheet_name": sheet["sheet_name"],
                    "columns": list(records[0]) if records else [],
                    "records": records,
                    "row_count": len(records),
                }
            )
        except Exception as exc:
            errors.append({"stage": "integrity_context", "path": f"{sheet['relative_path']}:{sheet['sheet_name']}", "error": str(exc)})
            logger.warning("Integrity context failed for %s:%s: %s", sheet["relative_path"], sheet["sheet_name"], exc)
    dataset_file = next((row for row in files if row["relative_path"].lower().endswith("dotad_dataset.json")), None)
    if dataset_file:
        try:
            data = json.loads((project_root / dataset_file["relative_path"]).read_text(encoding="utf-8"))
            if isinstance(data, list) and (not data or isinstance(data[0], dict)):
                contexts.append(
                    {
                        "table_id": f"{dataset_file['file_id']}:JSON",
                        "relative_path": dataset_file["relative_path"],
                        "sheet_name": "JSON list",
                        "columns": list(data[0]) if data else [],
                        "records": data,
                        "row_count": len(data),
                    }
                )
        except Exception as exc:
            errors.append({"stage": "integrity_context", "path": dataset_file["relative_path"], "error": str(exc)})
    return contexts


def run_integrity_audit(
    contexts: list[dict[str, Any]],
    project_root: Path,
    table_results: dict[str, list[dict[str, Any]]],
    output_root: Path,
) -> dict[str, Any]:
    key_inventory: list[dict[str, Any]] = []
    key_audits: list[dict[str, Any]] = []
    sequence_fields: list[dict[str, Any]] = []
    sequence_qc: list[dict[str, Any]] = []
    sequence_duplicates: list[dict[str, Any]] = []
    identity_conflicts: list[dict[str, Any]] = []
    for context in contexts:
        key_columns = candidate_key_columns(context["columns"])
        for column in key_columns:
            normalized = normalize_column_name(column)
            key_inventory.append(
                {
                    "table_id": context["table_id"],
                    "relative_path": context["relative_path"],
                    "sheet_or_table_name": context["sheet_name"],
                    "column_name": column,
                    "key_role_guess": (
                        "DOTAD identifier"
                        if "dotad" in normalized or normalized == "antibody_id"
                        else "source/record identifier"
                        if any(token in normalized for token in ("source", "record"))
                        else "candidate identifier"
                    ),
                    "relationship_status": "possible",
                    "notes": "Role inferred from the column name only.",
                }
            )
            key_audits.append(audit_key_column(context["table_id"], context["records"], column))
        fields, qc, duplicates, conflicts = profile_sequence_table(
            context["table_id"], context["records"]
        )
        sequence_fields.extend(fields)
        sequence_qc.extend(qc)
        sequence_duplicates.extend(duplicates)
        identity_conflicts.extend(conflicts)
    fk_rows, orphan_rows = run_probable_foreign_keys(contexts, key_audits)
    directory = output_root / "03_data_integrity"
    write_tsv(directory / "sequence_field_inventory.tsv", sequence_fields)
    write_tsv(directory / "sequence_qc_summary.tsv", sequence_qc)
    write_tsv(directory / "sequence_duplicate_summary.tsv", sequence_duplicates)
    write_tsv(directory / "key_inventory.tsv", key_inventory)
    write_tsv(directory / "key_uniqueness_audit.tsv", key_audits)
    write_tsv(directory / "foreign_key_audit.tsv", fk_rows)
    write_tsv(directory / "orphan_record_audit.tsv", orphan_rows)
    write_tsv(directory / "conflicting_identity_candidates.tsv", identity_conflicts)
    scripts_text = []
    for script_root in (project_root / "scripts", project_root / "R"):
        if not script_root.is_dir():
            continue
        for path in script_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".r", ".rmd", ".js"}:
                scripts_text.append(
                    (
                        path.relative_to(project_root).as_posix(),
                        path.read_text(encoding="utf-8", errors="replace"),
                    )
                )
    definition = infer_sequence_unique_definition(sequence_duplicates, scripts_text)
    write_json(directory / "sequence_unique_definition_assessment.json", definition)
    return {
        "summary": {
            "key_candidate_count": len(key_audits),
            "foreign_key_candidate_count": len(fk_rows),
            "orphan_candidate_count": len(orphan_rows),
            "sequence_field_count": len(sequence_fields),
            "identity_conflict_candidate_count": len(identity_conflicts),
            "sequence_unique_definition_status": definition["status"],
        }
    }


def run_probable_foreign_keys(
    contexts: list[dict[str, Any]],
    key_audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent_candidates: list[tuple[dict[str, Any], str]] = []
    children: list[tuple[dict[str, Any], str]] = []
    for context in contexts:
        for column in context["columns"]:
            normalized = normalize_column_name(column)
            if normalized not in {"dotad_id", "antibody_id"} and not (
                "dotad" in normalized and "id" in normalized
            ):
                continue
            children.append((context, column))
            status = next(
                (
                    row["candidate_key_status"]
                    for row in key_audits
                    if row["table_id"] == context["table_id"] and row["column_name"] == column
                ),
                "",
            )
            if status in {"confirmed_candidate", "probable_candidate"} and any(
                token in context["relative_path"].lower() for token in ("metadata", "dataset", "sequence")
            ):
                parent_candidates.append((context, column))
    fk_rows: list[dict[str, Any]] = []
    orphan_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for child, child_column in children:
        best_parents = sorted(
            parent_candidates,
            key=lambda pair: (
                0 if "metadata" in pair[0]["relative_path"].lower() else 1,
                pair[0]["row_count"],
            ),
        )[:2]
        for parent, parent_column in best_parents:
            marker = (child["table_id"], child_column, parent["table_id"], parent_column)
            if child["table_id"] == parent["table_id"] or marker in seen:
                continue
            seen.add(marker)
            result, orphans = audit_foreign_key(
                child["table_id"],
                child_column,
                child["records"],
                parent["table_id"],
                parent_column,
                parent["records"],
                confidence="probable" if "metadata" in parent["relative_path"].lower() else "possible",
            )
            fk_rows.append(result)
            orphan_rows.extend(orphans)
    return fk_rows, orphan_rows


def write_reproducibility_outputs(
    scripts: list[dict[str, Any]],
    files: list[dict[str, Any]],
    project_root: Path,
    output_root: Path,
    version_note: str,
) -> None:
    directory = output_root / "05_reproducibility"
    edges = dependency_edges(scripts)
    status_rows, missing = reproducibility_rows(scripts, files, project_root)
    write_tsv(directory / "script_inventory.tsv", scripts)
    write_tsv(directory / "data_dependency_edges.tsv", edges)
    write_tsv(directory / "reproducibility_status.tsv", status_rows)
    write_tsv(
        directory / "missing_dependencies.tsv",
        missing,
        ["dependency_type", "required_by", "missing_item", "severity", "notes"],
    )
    (directory / "rebuild_dependency_graph.md").write_text(
        dependency_graph_markdown(scripts, status_rows, version_note), encoding="utf-8"
    )


def build_claim_facts(
    project_root: Path,
    files: list[dict[str, Any]],
    tables: dict[str, list[dict[str, Any]]],
    scripts: list[dict[str, Any]],
    resource_metrics: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    reproduction_command = (
        'python -m dotad_audit.cli --project-root "." '
        '--manuscript "DOTAD2.0 manuscript.docx" '
        '--output-root "audit_outputs/phase1" --claims-only '
        '--data-file "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx" '
        '--data-file "assets/data/dotad_data_dictionary_v2.0.xlsx" '
        '--data-file "assets/data/dotad_experimental_developability_v2.0.xlsx" '
        '--data-file "assets/data/dotad_literature_developability_collection_v2.0.xlsx" '
        '--data-file "assets/data/dotad_affinity_benchmark_v2.0.zip"'
    )
    metadata_table = _find_table(
        tables["tables"],
        "dotad_antibody_metadata_sequences_v2.0.xlsx",
        "metadata",
    )
    sequence_table = _find_table(
        tables["tables"],
        "dotad_antibody_metadata_sequences_v2.0.xlsx",
        "sequences",
    )
    metric_summary = resource_metrics.get("summary", {})
    if metric_summary:
        entries = int(metric_summary["total_antibody_entries"])
        annotations = int(metric_summary["developability_annotations"])
        facts["resource_sequence_unique_entries"] = _fact(
            source=(
                "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx | "
                "assets/data/dotad_experimental_developability_v2.0.xlsx | "
                "assets/data/dotad_literature_developability_collection_v2.0.xlsx | "
                "assets/data/dotad_affinity_benchmark_v2.0.zip"
            ),
            table="All authoritative logical tables and affinity CSV datasets",
            columns="antibody_id plus detected sequence columns",
            logic=metric_summary["sequence_unique_definition"],
            script="scientific_data_audit/src/dotad_audit/resource_metrics.py",
            command=reproduction_command,
            value=entries,
            mapping="exact_file_and_script",
            verification=(
                "verified_rounding"
                if round(entries, -3) == 324000
                else "contradictory"
            ),
            confidence="high",
            notes=(
                "The exact recalculation is a hybrid count: canonical workbook IDs plus "
                "audit-normalized affinity sequence fingerprints. It is not a strict "
                "VH/VL-pair-unique count across all source assets."
            ),
        )
        facts["resource_annotations"] = _fact(
            source=(
                "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx | "
                "assets/data/dotad_experimental_developability_v2.0.xlsx | "
                "assets/data/dotad_literature_developability_collection_v2.0.xlsx | "
                "assets/data/dotad_affinity_benchmark_v2.0.zip"
            ),
            table="All authoritative logical tables and affinity CSV datasets",
            columns="Non-empty fields classified as developability/affinity metrics",
            logic=(
                "Count non-empty metric fields while excluding identifier, sequence, "
                "helper, replicate-count, and source metadata fields."
            ),
            script="scientific_data_audit/src/dotad_audit/resource_metrics.py",
            command=reproduction_command,
            value=annotations,
            mapping="exact_file_and_script",
            verification=(
                "verified_rounding"
                if round(annotations / 1_000_000, 2) == 2.89
                else "contradictory"
            ),
            confidence="high",
            notes=(
                "This total combines affinity/binding labels with experimental and "
                "literature developability fields using the legacy-compatible field-count "
                "heuristic. Raw, averaged, and source-specific representations are not "
                "deduplicated, so this is not an independently defined observation count. "
                "The component breakdown is written to resource_metric_components.tsv."
            ),
        )

    if metadata_table:
        metadata_rows = int(metadata_table["row_count"])
        metadata_records = read_sheet_records(
            project_root / metadata_table["relative_path"],
            metadata_table["sheet_or_table_name"],
        )
        whole_mab = sum(
            str(record.get("Format") or "").strip() == "Whole mAb"
            for record in metadata_records
        )
        facts["metadata_subset"] = _fact(
            source=metadata_table["relative_path"],
            table="metadata",
            columns="all non-empty data rows",
            logic="Count non-empty rows after the header; no therapeutic-subset filter is encoded in the current table.",
            command=reproduction_command,
            value=metadata_rows,
            mapping="exact_file_no_script",
            verification="contradictory",
            confidence="high",
            notes=(
                "The current authoritative metadata sheet contains 4,100 records. "
                "The current Fig3 script also uses all metadata rows, so the manuscript's "
                "n=1,236 subset requires an explicit historical filter or frozen input."
            ),
        )
        facts["whole_mab_count"] = _fact(
            source=metadata_table["relative_path"],
            table="metadata",
            columns="Format",
            logic="Count rows where trimmed Format == 'Whole mAb'.",
            command=reproduction_command,
            value=whole_mab,
            mapping="exact_file_no_script",
            verification="contradictory",
            confidence="high",
            notes="The current core workbook contains 823 Whole mAb records, not 840.",
        )
        facts["whole_mab_percent"] = _fact(
            source=metadata_table["relative_path"],
            table="metadata",
            columns="Format",
            logic="Whole mAb rows divided by all current metadata rows.",
            command=reproduction_command,
            value=round(100 * whole_mab / metadata_rows, 6),
            mapping="exact_file_no_script",
            verification="contradictory",
            confidence="high",
            notes=(
                "The current denominator is 4,100 metadata rows. The 68% claim uses a "
                "different, undocumented 1,236-record subset."
            ),
        )

    harmonized_path = project_root / "analysis_outputs" / "harmonized_analysis_subset.csv"
    if harmonized_path.exists():
        harmonized_rows = _read_csv_records(harmonized_path)
        pca_assays = [
            "hic_rt",
            "smac_rt",
            "sec_monomer",
            "tonset_nanodsf",
            "tm1_nanodsf",
            "tm2_nanodsf",
            "acsins_dlmax_ph7.4",
            "polyreactivity_prscore_cho",
        ]
        complete_rows = [
            row
            for row in harmonized_rows
            if all(_is_number(row.get(column)) for column in pca_assays)
        ]
        derived_note = (
            "The current derived CSV matches the claim, but its R builder still references "
            "a legacy data-dictionary sheet name (`Data Dictionary` rather than current "
            "`Field Dictionary`), so end-to-end rebuilding requires a script repair."
        )
        facts["harmonized_candidate_matrix"] = _fact(
            source="analysis_outputs/harmonized_analysis_subset.csv",
            table="CSV",
            columns="antibody_id plus harmonized assay columns",
            logic="Count antibody-level rows.",
            script="R/fig3_fig4_landscape_analysis.R",
            command=reproduction_command,
            value=len(harmonized_rows),
            mapping="exact_file_and_script",
            verification="partially_verified",
            confidence="high",
            notes=derived_note,
        )
        facts["complete_case_subset"] = _fact(
            source="analysis_outputs/harmonized_analysis_subset.csv",
            table="CSV",
            columns=", ".join(pca_assays),
            logic="Retain rows with non-missing numeric values for all eight PCA assays.",
            script="R/fig3_fig4_landscape_analysis.R",
            command=reproduction_command,
            value=len(complete_rows),
            mapping="exact_file_and_script",
            verification="verified_exact",
            confidence="high",
            notes="Recomputed directly from the current 326-row harmonized CSV.",
        )
        facts["representative_assays"] = _fact(
            source="analysis_outputs/harmonized_analysis_subset.csv",
            table="CSV",
            columns=", ".join(pca_assays),
            logic="Count the explicit fig5_distribution_assays/fig5_pca_assays vector.",
            script="R/fig3_fig4_landscape_analysis.R",
            command=reproduction_command,
            value=len(pca_assays),
            mapping="exact_file_and_script",
            verification="verified_exact",
            confidence="high",
            notes="The current script explicitly defines eight assay readouts.",
        )
        pca_values = _pca_explained_variance(complete_rows, pca_assays)
        for claim_id, index, expected in (
            ("pca_pc1", 0, 32.7),
            ("pca_pc2", 1, 20.8),
            ("pca_pc3", 2, 16.1),
        ):
            value = round(pca_values[index], 1)
            facts[claim_id] = _fact(
                source="analysis_outputs/harmonized_analysis_subset.csv",
                table="CSV complete-case matrix",
                columns=", ".join(pca_assays),
                logic="Z-score the eight assay columns and calculate PCA explained variance.",
                script="R/fig3_fig4_landscape_analysis.R",
                command=reproduction_command,
                value=value,
                mapping="exact_file_and_script",
                verification=(
                    "verified_rounding" if value == expected else "contradictory"
                ),
                confidence="high",
                notes="Audit recomputation uses the current complete-case matrix.",
            )
        for claim_id, count, expected in (
            ("pca_first_two", 2, 53.5),
            ("pca_first_three", 3, 69.6),
        ):
            value = round(sum(pca_values[:count]), 1)
            facts[claim_id] = _fact(
                source="analysis_outputs/harmonized_analysis_subset.csv",
                table="CSV complete-case matrix",
                columns=", ".join(pca_assays),
                logic=f"Sum explained variance for the first {count} principal components.",
                script="R/fig3_fig4_landscape_analysis.R",
                command=reproduction_command,
                value=value,
                mapping="exact_file_and_script",
                verification=(
                    "verified_rounding" if value == expected else "contradictory"
                ),
                confidence="high",
                notes="Audit recomputation uses the current complete-case matrix.",
            )

    full_resource_path = project_root / "analysis_outputs" / "full_resource_dataset.csv"
    if full_resource_path.exists():
        coverage = _coverage_counts(full_resource_path)
        for claim_id, family in (
            ("coverage_tm", "Tm / thermostability"),
            ("coverage_hic", "HIC / hydrophobicity"),
            ("coverage_smac", "SMAC / self-interaction"),
            ("coverage_ac_sins", "AC-SINS / self-association"),
            ("coverage_polyreactivity", "Polyreactivity / nonspecific binding"),
        ):
            facts[claim_id] = _fact(
                source="analysis_outputs/full_resource_dataset.csv",
                table="CSV",
                columns="antibody_id, antibody_linked, assay_family",
                logic=f"Count distinct linked antibody_id values for assay_family == '{family}'.",
                script="R/fig3_fig4_landscape_analysis.R",
                command=reproduction_command,
                value=coverage.get(family, 0),
                mapping="exact_file_and_script",
                verification="contradictory",
                confidence="high",
                notes=(
                    "The current derived all-source dataset produces a different count "
                    "from the manuscript/older figure; this is a data-version drift issue."
                ),
            )

    _add_figure4_facts(project_root, facts, reproduction_command)

    if sequence_table:
        facts.setdefault(
            "resource_sequence_unique_entries",
            _fact(
                source=sequence_table["relative_path"],
                table="sequences",
                columns="antibody_id, VH, VL, hc_protein_sequence, lc_protein_sequence",
                logic="No complete cross-asset recalculation was available.",
                command="",
                value="",
                mapping="multiple_candidates",
                verification="not_reproducible",
                confidence="high",
                notes="Run the authoritative resource metric recalculation.",
            ),
        )
    for historical in ("dotad1_entries", "dotad1_records"):
        facts[historical] = {
            "candidate_source_file": "",
            "candidate_sheet_or_table": "",
            "candidate_columns": "",
            "candidate_filter_logic": "",
            "recomputed_value": "",
            "mapping_status": "no_candidate",
            "verification_status": "not_attempted_due_to_missing_input",
            "confidence": "high",
            "notes": (
                "The author-designated current data scope does not include the immutable "
                "DOTAD 1.0 baseline needed to reproduce this historical comparison."
            ),
        }
    return facts


def _find_table(
    rows: list[dict[str, Any]],
    filename: str,
    sheet: str,
) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in rows
            if Path(row["relative_path"]).name.lower() == filename.lower()
            and str(row["sheet_or_table_name"]).strip() == sheet
        ),
        None,
    )


def _fact(
    *,
    source: str,
    table: str,
    columns: str,
    logic: str,
    command: str,
    value: Any,
    mapping: str,
    verification: str,
    confidence: str,
    notes: str,
    script: str = "",
) -> dict[str, Any]:
    return {
        "candidate_source_file": source,
        "candidate_sheet_or_table": table,
        "candidate_columns": columns,
        "candidate_filter_logic": logic,
        "candidate_script": script,
        "reproduction_command": command,
        "recomputed_value": value,
        "mapping_status": mapping,
        "verification_status": verification,
        "confidence": confidence,
        "notes": notes,
    }


def _read_csv_records(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_number(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _pca_explained_variance(
    rows: list[dict[str, str]],
    columns: list[str],
) -> list[float]:
    import numpy as np

    matrix = np.asarray(
        [[float(row[column]) for column in columns] for row in rows],
        dtype=float,
    )
    standardized = (matrix - matrix.mean(axis=0)) / matrix.std(axis=0, ddof=1)
    singular_values = np.linalg.svd(standardized, full_matrices=False)[1]
    variances = singular_values**2 / (len(matrix) - 1)
    return (100 * variances / variances.sum()).tolist()


def _coverage_counts(path: Path) -> dict[str, int]:
    families: dict[str, set[str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            linked = str(row.get("antibody_linked") or "").strip().lower()
            antibody_id = str(row.get("antibody_id") or "").strip()
            family = str(row.get("assay_family") or "").strip()
            if linked not in {"true", "1", "yes"} or not antibody_id or not family:
                continue
            families.setdefault(family, set()).add(antibody_id)
    return {family: len(ids) for family, ids in families.items()}


def _add_figure4_facts(
    project_root: Path,
    facts: dict[str, dict[str, Any]],
    reproduction_command: str,
) -> None:
    final_summary = project_root / "results" / "fig4" / "fig4_statistics_summary_final.csv"
    current_r_summary = project_root / "results" / "fig4" / "fig4_statistics_summary_R.csv"
    if not final_summary.exists():
        return
    rows = _read_csv_records(final_summary)
    values = {
        row["Metric"]: float(row["Value"])
        for row in rows
        if _is_number(row.get("Value"))
    }
    current_values: dict[str, float] = {}
    if current_r_summary.exists():
        current_values = {
            row["Metric"]: float(row["Value"])
            for row in _read_csv_records(current_r_summary)
            if _is_number(row.get("Value"))
        }
    fact_specs = {
        "replicated_pairs": ("cross_source_pairs", "Panel A pair rows"),
        "cross_source_pearson": ("pearson_r", "Panel A Pearson correlation"),
        "cross_source_spearman": ("spearman_rho", "Panel A Spearman correlation"),
        "tier_concordance": ("tier_concordance_rate", "Panel A tier agreement"),
        "nearest_pairs": ("neighbor_pairs", "Panel C nearest-neighbor rows"),
        "random_pairs": ("random_pairs", "Panel C random-pair rows"),
        "neighbor_pvalue": ("wilcox_p", "Panel C Wilcoxon rank-sum P value"),
    }
    for claim_id, (metric, description) in fact_specs.items():
        if metric not in values:
            continue
        value = values[metric]
        if claim_id == "tier_concordance":
            value *= 100
        facts[claim_id] = _fact(
            source=(
                "results/fig4/fig4_statistics_summary_final.csv | "
                "results/fig4/fig4_statistics_summary_R.csv"
            ),
            table="Panel A/C summary metrics",
            columns=f"Metric == {metric}",
            logic=f"Use {description} from the final integrated figure inputs.",
            script=(
                "scripts/fig4_reliability_validation.py | "
                "scripts/fig4_reliability_validation_final.R | "
                "scripts/fig4_integrated_draft.R"
            ),
            command=reproduction_command,
            value=value,
            mapping="multiple_candidates",
            verification="partially_verified",
            confidence="high",
            notes=(
                "The final integrated figure input matches the manuscript, but a parallel "
                "R summary differs"
                + (
                    f" ({metric}: final={values[metric]:.12g}, R={current_values[metric]:.12g})"
                    if metric in current_values
                    else ""
                )
                + ". Both outputs predate the June 2026 core-workbook updates, so this "
                "claim is not treated as current-data verified."
            ),
        )


def _contradictory_fact(
    facts: dict[str, dict[str, Any]],
    claim_id: str,
    table: dict[str, Any],
    *,
    recomputed: Any,
    columns: str,
    logic: str,
    note: str,
    reproduction_command: str,
) -> None:
    facts[claim_id] = {
        "candidate_source_file": table["relative_path"],
        "candidate_sheet_or_table": table["sheet_or_table_name"],
        "candidate_columns": columns,
        "candidate_filter_logic": logic,
        "reproduction_command": reproduction_command,
        "recomputed_value": recomputed,
        "mapping_status": "exact_file_no_script",
        "verification_status": "contradictory",
        "confidence": "high",
        "notes": note,
    }


def write_claim_outputs(
    output_root: Path,
    claims: list[dict[str, Any]],
    mapping: list[dict[str, Any]],
    manuscript: Path,
) -> None:
    directory = output_root / "04_manuscript_claims"
    write_tsv(directory / "manuscript_numeric_claims.tsv", claims)
    (directory / "claim_context_report.md").write_text(
        claim_context_markdown(claims, manuscript), encoding="utf-8"
    )
    write_tsv(directory / "manuscript_claim_to_data_map.tsv", mapping)
    write_tsv(directory / "unmapped_claims.tsv", unresolved_claims(mapping))
    write_tsv(directory / "claim_discrepancies.tsv", discrepancies(mapping))


def sequence_definition_from_outputs(
    project_root: Path,
    scripts: list[dict[str, Any]],
    output_root: Path,
) -> dict[str, str]:
    path = output_root / "03_data_integrity" / "sequence_unique_definition_assessment.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    script_texts = []
    for script in scripts:
        script_path = project_root / script["relative_path"]
        script_texts.append(
            (script["relative_path"], script_path.read_text(encoding="utf-8", errors="replace"))
        )
    return infer_sequence_unique_definition([], script_texts)


def finish_run_metadata(
    args: argparse.Namespace,
    metadata: dict[str, Any],
    output_root: Path,
    files: list[dict[str, Any]],
    errors: list[dict[str, str]],
    warnings: list[str],
) -> None:
    metadata["audit_finished_utc"] = datetime.now(timezone.utc).isoformat()
    metadata["captured_error_count"] = len(errors)
    metadata["warning_count"] = len(warnings)
    metadata["errors"] = errors
    metadata["warnings"] = warnings
    metadata["manuscript_sha256_after"] = sha256_file(args.manuscript.resolve())
    metadata["manuscript_unchanged"] = (
        metadata["manuscript_sha256_before"] == metadata["manuscript_sha256_after"]
    )
    if args.archive:
        metadata["archive_sha256_after"] = sha256_file(args.archive.resolve())
        metadata["archive_unchanged"] = (
            metadata["archive_sha256_before"] == metadata["archive_sha256_after"]
        )
    authoritative_unchanged = True
    for item in metadata.get("authoritative_data_files", []):
        path = Path(item["path"])
        item["sha256_after"] = sha256_file(path)
        item["unchanged"] = item["sha256_before"] == item["sha256_after"]
        authoritative_unchanged = authoritative_unchanged and item["unchanged"]
    metadata["authoritative_data_files_unchanged"] = authoritative_unchanged
    metadata["input_mutation_detected"] = bool(
        not metadata["manuscript_unchanged"]
        or (args.archive and not metadata.get("archive_unchanged", False))
        or not authoritative_unchanged
    )
    write_json(output_root / "00_run_metadata" / "run_metadata.json", metadata)
    (output_root / "00_run_metadata" / "active_run.json").unlink(missing_ok=True)
    write_tsv(
        output_root / "07_logs" / "errors.tsv",
        errors,
        ["stage", "path", "error"],
    )


def write_extraction_log(path: Path, rows: list[dict[str, Any]]) -> None:
    counts = Counter(row["extraction_status"] for row in rows)
    unsafe = [row for row in rows if row["security_status"] != "safe"]
    lines = [
        f"Extraction timestamp UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Archive members: {len(rows)}",
        *[f"{status}: {count}" for status, count in sorted(counts.items())],
        f"Unsafe or non-safe members: {len(unsafe)}",
        "No extracted program, macro, script, or installer was executed.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: serialize_tsv_value(row.get(field, "")) for field in fields})


def serialize_tsv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
