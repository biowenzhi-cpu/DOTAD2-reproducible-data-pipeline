from __future__ import annotations

from dotad_audit.report_builder import build_gap_outputs


def test_gap_report_builds_for_exact_authoritative_scope() -> None:
    paths = [
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    ]
    files = [
        {
            "relative_path": path,
            "filename": path.rsplit("/", 1)[-1],
            "read_status": "readable",
        }
        for path in paths
    ]

    report, matrix, requests, release = build_gap_outputs(
        archive_version_note="Five-file fixture.",
        sequence_definition={
            "status": "manual_confirmation",
            "definition": "Fixture definition.",
            "evidence": "Fixture evidence.",
        },
        claim_map=[],
        script_inventory=[],
        files=files,
        dictionary_mismatch_count=1,
        dictionary_out_of_scope_count=5,
        resource_metric_summary={},
    )

    assert "Authoritative input alignment: **READY**" in report
    assert matrix
    assert requests
    assert "DOTAD2_scientific_data_release_v2.0.0" in release


def test_unreadable_authoritative_asset_is_not_marked_ready() -> None:
    paths = [
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    ]
    files = [
        {
            "relative_path": path,
            "filename": path.rsplit("/", 1)[-1],
            "read_status": "unreadable" if index == 0 else "readable",
        }
        for index, path in enumerate(paths)
    ]

    _, matrix, _, _ = build_gap_outputs(
        archive_version_note="Five-file fixture.",
        sequence_definition={
            "status": "manual_confirmation",
            "definition": "Fixture definition.",
            "evidence": "Fixture evidence.",
        },
        claim_map=[],
        script_inventory=[],
        files=files,
        dictionary_mismatch_count=0,
        dictionary_out_of_scope_count=0,
        resource_metric_summary={},
    )

    readability = next(
        row for row in matrix if row["deliverable"] == "Core workbook/archive readability"
    )
    assert readability["status"] == "BLOCKING"
    assert readability["evidence_present"] is False
