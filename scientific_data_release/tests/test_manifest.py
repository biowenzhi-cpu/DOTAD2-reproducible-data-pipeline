from __future__ import annotations

from pathlib import Path

from dotad_release.manifest import (
    write_checksums,
    write_core_qc,
    write_release_manifest,
)
from dotad_release.models import BuildContext, ExportResult
from dotad_release.workbook_reader import write_tsv


def _context(tmp_path: Path) -> BuildContext:
    return BuildContext(
        output_dir=tmp_path / "release",
        core_inputs={},
        sheet_roles={},
        input_shas={},
        release_version="v2.0.0",
        script_root=tmp_path,
    )


def _formal_result(context: BuildContext) -> ExportResult:
    path = context.output_dir / "metadata" / "antibody_metadata.tsv"
    rows = ({"record_id": "META:metadata:2", "antibody_id": "DOTAD-001"},)
    fields = ("record_id", "antibody_id")
    write_tsv(path, rows, fields)
    return ExportResult(
        "antibody_metadata", path, fields, rows, "one non-empty metadata source row"
    )


def test_manifest_and_qc_are_deterministic_and_exclude_companion_from_core_counts(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    formal = _formal_result(context)
    companion_path = (
        context.output_dir / "companion" / "affinity_collection_manifest_draft.tsv"
    )
    write_tsv(
        companion_path,
        [{"record_id": "COMP:one"}],
        ["record_id"],
    )
    companion = ExportResult(
        "affinity_collection_manifest_draft",
        companion_path,
        ("record_id",),
        ({"record_id": "COMP:one"},),
        "one companion metadata row",
    )
    counts, integrity = write_core_qc(context, [formal])
    assert {row["table_name"] for row in counts.rows} == {"antibody_metadata"}
    assert all("affinity" not in row["table_name"] for row in counts.rows)
    manifest_path = write_release_manifest(
        context,
        lineage_required=[formal],
        administrative=[counts, integrity, companion],
    )
    first = manifest_path.read_bytes()
    write_release_manifest(
        context,
        lineage_required=[formal],
        administrative=[counts, integrity, companion],
    )
    assert manifest_path.read_bytes() == first


def test_checksum_file_does_not_hash_itself(tmp_path: Path) -> None:
    root = tmp_path / "release"
    (root / "README_DRAFT.md").parent.mkdir(parents=True)
    (root / "README_DRAFT.md").write_text("stable\n", encoding="utf-8")
    path = write_checksums(root)
    text = path.read_text(encoding="utf-8")
    assert "README_DRAFT.md" in text
    assert "checksums_sha256.txt" not in text


def test_core_counts_report_record_and_identity_metrics_separately(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    metadata_path = context.output_dir / "metadata" / "antibody_metadata.tsv"
    metadata_rows = (
        {"record_id": "META:metadata:2", "antibody_id": "DOTAD-001"},
        {"record_id": "META:metadata:3", "antibody_id": "DOTAD-001"},
        {"record_id": "META:metadata:4", "antibody_id": ""},
    )
    write_tsv(
        metadata_path,
        metadata_rows,
        ("record_id", "antibody_id"),
    )
    metadata = ExportResult(
        "antibody_metadata",
        metadata_path,
        ("record_id", "antibody_id"),
        metadata_rows,
        "one non-empty metadata source row",
    )
    sequence_path = context.output_dir / "sequences" / "antibody_sequences.tsv"
    sequence_rows = (
        {
            "record_id": "SEQ:sequences:2",
            "sequence_class": "paired_vh_vl",
        },
        {
            "record_id": "SEQ:sequences:3",
            "sequence_class": "heavy_only",
        },
    )
    write_tsv(
        sequence_path,
        sequence_rows,
        ("record_id", "sequence_class"),
    )
    sequences = ExportResult(
        "antibody_sequences",
        sequence_path,
        ("record_id", "sequence_class"),
        sequence_rows,
        "one non-empty sequence source row",
    )

    counts, _ = write_core_qc(context, [metadata, sequences])
    metrics = {
        row["metric_name"]: row["metric_value"]
        for row in counts.rows
        if row["metric_name"]
    }

    assert metrics["metadata_record_count"] == 3
    assert metrics["non_missing_antibody_id_count"] == 2
    assert metrics["unique_antibody_id_count"] == 1
    assert metrics["duplicate_antibody_id_count"] == 1
    assert metrics["sequence_record_count"] == 2
    assert metrics["records_with_paired_vh_vl"] == 1
    assert metrics["records_with_heavy_only"] == 1
