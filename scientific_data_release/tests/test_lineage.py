from __future__ import annotations

from pathlib import Path

import pytest

from dotad_release.lineage import _script_for_table, build_lineage_outputs
from dotad_release.models import BuildContext, ExportResult


def _context(tmp_path: Path) -> BuildContext:
    return BuildContext(
        output_dir=tmp_path / "release",
        core_inputs={},
        sheet_roles={},
        input_shas={},
        release_version="v2.0.0",
        script_root=tmp_path / "scientific_data_release" / "src",
    )


def test_lineage_coverage_is_one_for_source_backed_rows(tmp_path: Path) -> None:
    result = ExportResult(
        table_name="antibody_metadata",
        path=tmp_path / "metadata.tsv",
        fieldnames=("record_id",),
        rows=(
            {
                "record_id": "META:metadata:2",
                "source_workbook": "metadata.xlsx",
                "source_input_sha256": "a" * 64,
                "source_sheet": "metadata",
                "source_excel_row": 2,
                "source_columns": "*",
            },
        ),
        row_definition="one source row",
    )
    lineage, coverage, rules = build_lineage_outputs(
        _context(tmp_path), [result], "abc123"
    )
    assert len(lineage.rows) == 1
    assert lineage.rows[0]["output_record_id"] == "META:metadata:2"
    assert lineage.rows[0]["transformation_rule_id"] == "COPY_WITH_METADATA"
    assert coverage.rows[0] == {
        "output_table": "antibody_metadata",
        "released_rows": 1,
        "rows_with_lineage": 1,
        "rows_without_lineage": 0,
        "coverage_fraction": 1.0,
        "lineage_required": "true",
    }
    assert rules.rows


def test_missing_source_metadata_fails_lineage_build(tmp_path: Path) -> None:
    result = ExportResult(
        table_name="antibody_sequences",
        path=tmp_path / "sequences.tsv",
        fieldnames=("record_id",),
        rows=({"record_id": "SEQ:sequences:2"},),
        row_definition="one source row",
    )
    with pytest.raises(ValueError, match="cannot be traced"):
        build_lineage_outputs(_context(tmp_path), [result], "abc123")


def test_sequence_fingerprint_uses_explicit_normalization_rule(
    tmp_path: Path,
) -> None:
    result = ExportResult(
        table_name="sequence_fingerprints",
        path=tmp_path / "fingerprints.tsv",
        fieldnames=("record_id",),
        rows=(
            {
                "record_id": "SEQFP:abc",
                "source_workbook": "sequences.xlsx",
                "source_input_sha256": "b" * 64,
                "source_sheet": "sequences",
                "source_excel_row": 7,
                "source_columns": "VH|VL",
            },
        ),
        row_definition="one fingerprint",
    )
    lineage, _, _ = build_lineage_outputs(_context(tmp_path), [result], "abc123")
    assert (
        lineage.rows[0]["transformation_rule_id"]
        == "NORMALIZE_SEQUENCE_FOR_HASH"
    )


def test_lineage_resolves_referenced_source_records_recursively(
    tmp_path: Path,
) -> None:
    metadata = ExportResult(
        table_name="antibody_metadata",
        path=tmp_path / "metadata.tsv",
        fieldnames=("record_id",),
        rows=(
            {
                "record_id": "META:metadata:2",
                "source_workbook": "metadata.xlsx",
                "source_input_sha256": "a" * 64,
                "source_sheet": "metadata",
                "source_excel_row": 2,
            },
        ),
        row_definition="one source row",
    )
    candidates = ExportResult(
        table_name="identity_mapping_candidates",
        path=tmp_path / "candidates.tsv",
        fieldnames=("record_id", "source_record_ids"),
        rows=(
            {
                "record_id": "IDCAND:one",
                "candidate_id": "IDCAND:one",
                "source_record_ids": "META:metadata:2",
            },
        ),
        row_definition="one identity review candidate",
    )
    review = ExportResult(
        table_name="identity_review_queue",
        path=tmp_path / "review.tsv",
        fieldnames=("record_id", "candidate_id"),
        rows=(
            {
                "record_id": "IDREVIEW:one",
                "candidate_id": "IDCAND:one",
            },
        ),
        row_definition="one identity review item",
    )

    lineage, coverage, _ = build_lineage_outputs(
        _context(tmp_path), [metadata, candidates, review], "abc123"
    )

    assert {
        (row["output_table"], row["output_record_id"], row["input_excel_row"])
        for row in lineage.rows
    } == {
        ("antibody_metadata", "META:metadata:2", 2),
        ("identity_mapping_candidates", "IDCAND:one", 2),
        ("identity_review_queue", "IDREVIEW:one", 2),
    }
    assert all(row["coverage_fraction"] == 1.0 for row in coverage.rows)


def test_dictionary_source_field_aliases_are_valid_lineage(
    tmp_path: Path,
) -> None:
    result = ExportResult(
        table_name="field_dictionary",
        path=tmp_path / "field_dictionary.tsv",
        fieldnames=("record_id",),
        rows=(
            {
                "record_id": "DICT:field_dictionary:2",
                "source_dictionary_workbook": "dictionary.xlsx",
                "source_input_sha256": "d" * 64,
                "source_dictionary_sheet": "Field Dictionary",
                "source_excel_row": 2,
            },
        ),
        row_definition="one dictionary source row",
    )

    lineage, coverage, _ = build_lineage_outputs(
        _context(tmp_path), [result], "abc123"
    )

    assert lineage.rows[0]["input_file"] == "dictionary.xlsx"
    assert lineage.rows[0]["input_sheet"] == "Field Dictionary"
    assert coverage.rows[0]["coverage_fraction"] == 1.0


def test_source_summary_exclusion_cases_use_experimental_export_lineage() -> None:
    assert (
        _script_for_table("source_summary_exclusion_cases")
        == "dotad_release/experimental_export.py"
    )
