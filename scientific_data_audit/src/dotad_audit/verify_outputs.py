from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_AUTHORITATIVE_RELATIVE_PATHS = frozenset(
    {
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    }
)
REQUIRED_OUTPUTS = (
    "00_run_metadata/archive_manifest.tsv",
    "00_run_metadata/run_metadata.json",
    "01_file_inventory/file_inventory.tsv",
    "01_file_inventory/file_hashes.tsv",
    "01_file_inventory/duplicate_file_groups.tsv",
    "01_file_inventory/largest_files.tsv",
    "01_file_inventory/extension_summary.tsv",
    "02_table_inventory/workbook_inventory.tsv",
    "02_table_inventory/sheet_inventory.tsv",
    "02_table_inventory/table_inventory.tsv",
    "02_table_inventory/column_dictionary_draft.tsv",
    "02_table_inventory/table_profile_summary.tsv",
    "03_data_integrity/sequence_field_inventory.tsv",
    "03_data_integrity/sequence_qc_summary.tsv",
    "03_data_integrity/sequence_duplicate_summary.tsv",
    "03_data_integrity/key_inventory.tsv",
    "03_data_integrity/key_uniqueness_audit.tsv",
    "03_data_integrity/foreign_key_audit.tsv",
    "03_data_integrity/orphan_record_audit.tsv",
    "03_data_integrity/conflicting_identity_candidates.tsv",
    "03_data_integrity/data_dictionary_inventory_crosscheck.tsv",
    "04_manuscript_claims/manuscript_numeric_claims.tsv",
    "04_manuscript_claims/manuscript_claim_to_data_map.tsv",
    "04_manuscript_claims/unmapped_claims.tsv",
    "04_manuscript_claims/claim_discrepancies.tsv",
    "04_manuscript_claims/claim_context_report.md",
    "05_reproducibility/script_inventory.tsv",
    "05_reproducibility/data_dependency_edges.tsv",
    "05_reproducibility/rebuild_dependency_graph.md",
    "05_reproducibility/reproducibility_status.tsv",
    "05_reproducibility/missing_dependencies.tsv",
    "05_reproducibility/benchmark_source_count_audit.tsv",
    "05_reproducibility/resource_metric_components.tsv",
    "05_reproducibility/resource_metric_recalculation.json",
    "06_scientific_data_gaps/Scientific_Data_gap_report.md",
    "06_scientific_data_gaps/deliverable_status_matrix.tsv",
    "06_scientific_data_gaps/manual_information_requests.tsv",
    "06_scientific_data_gaps/proposed_release_structure.md",
    "07_logs/audit.log",
    "07_logs/errors.tsv",
    "07_logs/extraction.log",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def immutable_hashes_match(record: dict[str, Any], current: str) -> bool:
    before = record.get("sha256_before", "")
    after = record.get("sha256_after", "")
    return (
        bool(before)
        and before == after == current
        and record.get("unchanged") is True
    )


def stale_required_outputs(
    output_root: Path,
    started: datetime,
    required_outputs: tuple[str, ...] = REQUIRED_OUTPUTS,
) -> list[str]:
    return [
        relative
        for relative in required_outputs
        if (output_root / relative).exists()
        and datetime.fromtimestamp(
            (output_root / relative).stat().st_mtime,
            tz=timezone.utc,
        )
        < started
    ]


def verify_output_tree(output_root: Path) -> dict[str, Any]:
    metadata = json.loads(
        (output_root / "00_run_metadata" / "run_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    missing_outputs = [
        relative for relative in REQUIRED_OUTPUTS if not (output_root / relative).exists()
    ]
    started = datetime.fromisoformat(metadata["audit_started_utc"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    stale_outputs = stale_required_outputs(output_root, started)

    authoritative_hash_checks = []
    for record in metadata.get("authoritative_data_files", []):
        path = Path(record["path"])
        current = sha256_file(path)
        before = record.get("sha256_before", "")
        after = record.get("sha256_after", "")
        authoritative_hash_checks.append(
            {
                "path": str(path),
                "relative_path": record.get("relative_path", ""),
                "sha256_before": before,
                "sha256_after": after,
                "current_sha256": current,
                "unchanged": immutable_hashes_match(record, current),
            }
        )

    manuscript_path = Path(metadata["manuscript_path"])
    manuscript_current_hash = sha256_file(manuscript_path)
    manuscript_before = metadata.get("manuscript_sha256_before", "")
    manuscript_after = metadata.get("manuscript_sha256_after", "")
    manuscript_unchanged = (
        bool(manuscript_before)
        and manuscript_before == manuscript_after == manuscript_current_hash
        and metadata.get("manuscript_unchanged") is True
    )

    files = read_tsv(output_root / "01_file_inventory" / "file_inventory.tsv")
    workbooks = read_tsv(
        output_root / "02_table_inventory" / "workbook_inventory.tsv"
    )
    sheets = read_tsv(output_root / "02_table_inventory" / "sheet_inventory.tsv")
    tables = read_tsv(output_root / "02_table_inventory" / "table_inventory.tsv")
    claims = read_tsv(
        output_root / "04_manuscript_claims" / "manuscript_claim_to_data_map.tsv"
    )
    archive = read_tsv(output_root / "00_run_metadata" / "archive_manifest.tsv")
    dictionary = read_tsv(
        output_root
        / "03_data_integrity"
        / "data_dictionary_inventory_crosscheck.tsv"
    )
    benchmark = read_tsv(
        output_root
        / "05_reproducibility"
        / "benchmark_source_count_audit.tsv"
    )
    resource = json.loads(
        (
            output_root
            / "05_reproducibility"
            / "resource_metric_recalculation.json"
        ).read_text(encoding="utf-8")
    )

    table_status = Counter(row.get("read_status", "") for row in tables)
    claim_status = Counter(row.get("verification_status", "") for row in claims)
    unsafe_members = sum(
        row.get("security_status", "") != "safe" for row in archive
    )
    dictionary_mismatches = sum(
        row.get("status", "") == "mismatch" for row in dictionary
    )
    benchmark_summary = next(
        (
            row
            for row in benchmark
            if row.get("record_type") == "summary"
        ),
        {},
    )
    metadata_scope = {
        row.get("relative_path", "").replace("\\", "/").lower()
        for row in authoritative_hash_checks
    }
    inventory_scope = {
        row.get("relative_path", "").replace("\\", "/").lower() for row in files
    }

    checks = {
        "required_outputs_present": not missing_outputs,
        "required_outputs_from_current_full_run": (
            metadata.get("mode") == "full"
            and bool(metadata.get("audit_finished_utc"))
            and "fatal_error" not in metadata
            and not (output_root / "00_run_metadata" / "active_run.json").exists()
            and not stale_outputs
        ),
        "authoritative_scope_exact": (
            metadata_scope == EXPECTED_AUTHORITATIVE_RELATIVE_PATHS
            and inventory_scope == EXPECTED_AUTHORITATIVE_RELATIVE_PATHS
        ),
        "authoritative_hashes_unchanged": all(
            row["unchanged"] for row in authoritative_hash_checks
        )
        and metadata.get("authoritative_data_files_unchanged") is True,
        "manuscript_hash_unchanged": manuscript_unchanged,
        "input_mutation_not_detected": metadata.get("input_mutation_detected") is False,
        "all_tables_readable": set(table_status) <= {"readable"},
        "no_captured_errors": metadata.get("captured_error_count") == 0,
        "archive_members_safe": bool(archive) and unsafe_members == 0,
        "data_dictionary_mismatch_count_matches_run_metadata": (
            dictionary_mismatches
            == metadata.get("data_dictionary_crosscheck_mismatch_count")
        ),
        "network_access_not_used": metadata.get("network_access_used") is False,
        "source_scripts_not_executed": metadata.get("source_scripts_executed") is False,
    }
    return {
        "required_outputs": len(REQUIRED_OUTPUTS),
        "missing_outputs": missing_outputs,
        "stale_outputs": stale_outputs,
        "authoritative_files": authoritative_hash_checks,
        "manuscript_path": str(manuscript_path),
        "manuscript_current_sha256": manuscript_current_hash,
        "manuscript_sha256_before": manuscript_before,
        "manuscript_sha256_after": manuscript_after,
        "manuscript_unchanged": manuscript_unchanged,
        "inventoried_files": len(files),
        "workbooks": len(workbooks),
        "worksheets": len(sheets),
        "tables": len(tables),
        "table_read_status": dict(table_status),
        "archive_members_including_nested": len(archive),
        "unsafe_archive_members": unsafe_members,
        "data_dictionary_crosscheck_rows": len(dictionary),
        "data_dictionary_mismatches": dictionary_mismatches,
        "all_claims": len(claims),
        "claim_verification_status": dict(claim_status),
        "benchmark_generic_profiler_rows": benchmark_summary.get(
            "profiled_data_rows", ""
        ),
        "benchmark_readme_size_total": benchmark_summary.get(
            "readme_declared_size", ""
        ),
        "benchmark_logical_nonempty_source_rows": resource.get(
            "affinity_source_rows", ""
        ),
        "resource_metrics": resource,
        "checks": checks,
        "all_checks": "PASS" if all(checks.values()) else "FAIL",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independently verify a completed DOTAD audit output tree."
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = verify_output_tree(args.output_root.resolve())
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    log_path = (
        args.log.resolve()
        if args.log
        else args.output_root.resolve() / "07_logs" / "verification.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["all_checks"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
