from __future__ import annotations

from collections import Counter
from typing import Any


def build_gap_outputs(
    *,
    archive_version_note: str,
    sequence_definition: dict[str, str],
    claim_map: list[dict[str, Any]],
    script_inventory: list[dict[str, Any]],
    files: list[dict[str, Any]],
    benchmark_count_note: str = "",
    dictionary_mismatch_count: int = 0,
    dictionary_out_of_scope_count: int = 0,
    resource_metric_summary: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str]:
    resource_metric_summary = resource_metric_summary or {}
    paths = {row["relative_path"].lower() for row in files}
    expected_paths = {
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    }
    scope_complete = paths == expected_paths
    dictionary_present = "assets/data/dotad_data_dictionary_v2.0.xlsx" in paths
    primary_assets_readable = all(
        row["read_status"] == "readable" for row in files
    )
    contradiction_count = sum(
        row["verification_status"] == "contradictory" for row in claim_map
    )
    partial_count = sum(
        row["verification_status"] == "partially_verified" for row in claim_map
    )

    checks = [
        (
            "Authoritative DOTAD 2.0 data scope",
            "READY" if scope_complete else "BLOCKING",
            scope_complete,
            archive_version_note,
        ),
        (
            "Submission-ready static release bundle",
            "REBUILDABLE",
            False,
            "The five authoritative assets exist, but they are not yet assembled with a release README, licences, checksums, and provenance tables.",
        ),
        (
            "Release-level README",
            "MISSING",
            False,
            "A README worksheet exists inside the data dictionary, but no package-level README accompanies the five-file release.",
        ),
        (
            "CHANGELOG",
            "REBUILDABLE",
            dictionary_present,
            "The data dictionary contains a Change Log worksheet that can seed a standalone CHANGELOG.",
        ),
        (
            "Data dictionary and assay semantics",
            "MANUAL_CONFIRMATION" if dictionary_mismatch_count else "READY",
            dictionary_present,
            (
                f"The v2.0 dictionary is present, but {dictionary_mismatch_count} declared row/column or file-count entries do not match the current files."
                if dictionary_mismatch_count
                else "The v2.0 dictionary and assay endpoint dictionary are present."
            ),
        ),
        (
            "Source manifest",
            "REBUILDABLE",
            dictionary_present,
            "Literature Sources, link, and affinity README content can seed a source manifest; licence and row-link fields still need completion.",
        ),
        (
            "Record lineage",
            "BLOCKING",
            False,
            "No row-level lineage table links every released value to source file, source sheet/row, curation action, and harmonization step.",
        ),
        (
            "Licence/redistribution manifest",
            "BLOCKING",
            any("license" in path for path in paths),
            "The affinity and literature assets aggregate third-party data, but no unified per-source redistribution-rights manifest is included.",
        ),
        (
            "Release manifest",
            "MANUAL_CONFIRMATION" if dictionary_mismatch_count else "REBUILDABLE",
            dictionary_present,
            "A Release File Manifest worksheet exists but must be reconciled to the final deposited files and exact member counts.",
        ),
        (
            "SHA-256 checksums",
            "REBUILDABLE",
            False,
            "The audit generated stable hashes; publish them as a release checksum file after final freezing.",
        ),
        (
            "Core workbook/archive readability",
            "READY" if primary_assets_readable else "BLOCKING",
            primary_assets_readable,
            (
                "All five scoped primary assets were opened read-only; unreadable nested members, if any, are retained in the audit logs."
                if primary_assets_readable
                else "At least one scoped primary asset could not be opened read-only; see the inventory and error logs."
            ),
        ),
        (
            "Candidate key and relationship audit",
            "READY",
            True,
            "Candidate key, foreign-key, orphan, and sequence-conflict outputs were generated without modifying source data.",
        ),
        (
            "Identity reconciliation and ID mapping",
            "BLOCKING",
            False,
            "The five-file primary scope has no explicit source-ID to canonical-ID mapping table, while duplicate VH/VL candidates occur across antibody IDs.",
        ),
        (
            "Sequence-unique definition",
            "MANUAL_CONFIRMATION",
            sequence_definition.get("status") != "unable_to_confirm",
            (
                "The resource-count implementation uses a hybrid rule: canonical workbook IDs plus affinity sequence fingerprints with heavy/light, heavy-only, single-sequence, CDR, and tuple fallbacks. "
                "This should not be described as strict VH/VL-pair uniqueness without qualification."
            ),
        ),
        (
            "Assay endpoint dictionary",
            "READY" if dictionary_present else "BLOCKING",
            dictionary_present,
            "The Assay Endpoint Dictionary records endpoint, unit, family, comparability, transform, risk direction, and statistics-use fields.",
        ),
        (
            "Unit conversion and condition rules",
            "MANUAL_CONFIRMATION",
            dictionary_present,
            "Endpoint-level units and transforms exist, but a complete source-value conversion audit remains necessary for all heterogeneous literature endpoints.",
        ),
        (
            "Affinity favorable-direction audit",
            "BLOCKING",
            False,
            "The affinity README marks upward as favorable for heterogeneous KD, IC50, EC50, binding, and transformed endpoints; per-dataset direction/transform rules require explicit confirmation.",
        ),
        (
            "Manual curation audit trail",
            "BLOCKING",
            False,
            "No curator/reviewer/action/timestamp decision log is present.",
        ),
        (
            "Source traceability",
            "MANUAL_CONFIRMATION",
            (
                "assets/data/dotad_literature_developability_collection_v2.0.xlsx"
                in paths
            ),
            "Literature sheets and a link worksheet provide source-level context, but row-level citation coverage is not complete.",
        ),
        (
            "Benchmark split/leakage audit",
            "MISSING",
            False,
            "The scoped affinity v2.0 package is a source benchmark bundle and has no deterministic train/validation/test assignments or sequence-leakage audit.",
        ),
        (
            "Complete source-to-release processing code",
            "BLOCKING",
            bool(script_inventory),
            (
                f"{len(script_inventory)} scripts were statically inventoried. Several current-data builders exist, but no single pinned, end-to-end source-to-release workflow is complete; one figure script still expects a superseded data-dictionary sheet name."
            ),
        ),
        (
            "Pinned software environment",
            "MISSING",
            any(
                path.endswith(("requirements.txt", "renv.lock", "environment.yml", "poetry.lock"))
                for path in paths
            ),
            "No complete pinned Python and R environment for the data/figure pipeline is included in the five-file release scope.",
        ),
        (
            "Data Records readiness",
            "REBUILDABLE",
            scope_complete,
            "Current workbooks provide enough table-level content for Data Records, after row definitions and manifest discrepancies are resolved.",
        ),
        (
            "Technical Validation readiness",
            "BLOCKING" if contradiction_count else "REBUILDABLE",
            bool(claim_map),
            (
                f"The audit found {contradiction_count} contradictory and {partial_count} partially verified manuscript claims; current-data/figure version drift must be resolved."
            ),
        ),
        (
            "Usage Notes and reuse guidance",
            "MISSING",
            False,
            "No submission-ready release Usage Notes or per-source reuse constraints accompany the five primary assets.",
        ),
        (
            "Permanent repository DOI/accession",
            "MANUAL_CONFIRMATION",
            False,
            "A mutable website URL is available, but an immutable repository DOI/accession must be confirmed for submission.",
        ),
        (
            "Website/static release relationship",
            "MANUAL_CONFIRMATION",
            False,
            "Website JSON products are downstream of the core workbooks; their exact build/version relationship should be documented without treating them as primary data.",
        ),
        (
            "DOTAD 1.0 to 2.0 increment explanation",
            "MANUAL_CONFIRMATION",
            False,
            "The current five-file scope does not include the immutable DOTAD 1.0 baseline used for historical comparisons.",
        ),
    ]

    matrix = [
        {
            "deliverable_id": f"D{index:03d}",
            "deliverable": deliverable,
            "status": status,
            "evidence_present": exists,
            "evidence_path_or_basis": notes,
            "required_action": _action_for_status(status),
            "notes": "",
        }
        for index, (deliverable, status, exists, notes) in enumerate(checks, start=1)
    ]
    requests = manual_requests(sequence_definition, dictionary_mismatch_count)
    status_counts = Counter(row["status"] for row in matrix)
    claim_counts = Counter(row["verification_status"] for row in claim_map)
    report = "\n".join(
        [
            "# DOTAD 2.0 Scientific Data Phase 1 Gap Report",
            "",
            "## Executive Assessment",
            "",
            (
                f"- Authoritative input alignment: **{'READY' if scope_complete else 'BLOCKING'}**. "
                f"{archive_version_note}"
            ),
            "- Rebuildability assessment: **PARTIAL**. The current static data assets are present and auditable, but a submission-grade frozen release still requires provenance, licensing, identity mapping, pipeline repair, and manuscript/figure reconciliation.",
            *(
                [f"- Affinity benchmark count cross-check: {benchmark_count_note}"]
                if benchmark_count_note
                else []
            ),
            "",
            "## Status Summary",
            "",
            *[f"- {status}: {count}" for status, count in sorted(status_counts.items())],
            "",
            "## Manuscript Claim Verification Summary",
            "",
            *[f"- {status}: {count}" for status, count in sorted(claim_counts.items())],
            "",
            "## Highest-Priority Blocking Gaps",
            "",
            "1. Row-level provenance and a curator/reviewer decision audit are absent.",
            "2. Per-source licence and redistribution rights are not documented for the aggregated literature and affinity datasets.",
            "3. The public resource-size definition uses a hybrid canonical-ID/sequence-fingerprint rule; strict sequence uniqueness and canonical identity mapping need explicit documentation.",
            "4. Current v2.0 core data and several manuscript/figure counts have drifted: metadata composition, assay coverage, and parallel Figure 4 outputs are not mutually aligned.",
            "5. The end-to-end build is not currently one-command reproducible: affinity data require archive-aware input handling, software versions are not pinned, and the Fig3/Fig4 R script references a superseded dictionary sheet.",
            "",
            "## Specific Current-Data Evidence",
            "",
            "- The scoped primary data consist of four v2.0 workbooks plus `dotad_affinity_benchmark_v2.0.zip`; the previous v1.0-snapshot conclusion is withdrawn.",
            *(
                [
                    "- A read-only, legacy-compatible heuristic reconstruction from the "
                    "five authoritative assets gives "
                    f"{int(resource_metric_summary['total_antibody_entries']):,} antibody entries, "
                    f"{int(resource_metric_summary['data_points']):,} data points, "
                    f"{int(resource_metric_summary['developability_annotations']):,} counted "
                    "developability-related annotations, and "
                    f"{int(resource_metric_summary['source_records']):,} non-empty source records.",
                    "- The `data points` value counts non-empty cells, including identifiers, "
                    "sequences, and metadata; the annotation value counts metric-classified "
                    "fields across raw, averaged, and source-specific representations without "
                    "cross-representation deduplication. These are claim-reconstruction "
                    "figures, not independently defined scientific-observation counts.",
                    "- The affinity archive contributes "
                    f"{int(resource_metric_summary['affinity_source_rows']):,} logical non-empty "
                    "source rows after excluding detected preamble/blank rows; the generic table "
                    "inventory also preserves physical-row evidence for independent review.",
                ]
                if resource_metric_summary
                else []
            ),
            f"- The v2.0 data dictionary has {dictionary_mismatch_count} row/column or release-member count mismatches against the current files.",
            (
                "- The Release File Manifest also contains "
                f"{dictionary_out_of_scope_count} entries outside the five-file primary "
                "data scope; they are retained as `out_of_scope` records rather than "
                "silently treated as audited primary inputs."
            ),
            "- The metadata workbook contains 4,100 records and 823 `Whole mAb` rows, whereas the manuscript's older subset reports 1,236 and 840.",
            "- The current all-source derived dataset produces assay-coverage counts different from the manuscript's Figure 2 values.",
            "- The final integrated Figure 4 inputs and a parallel R output disagree for Panel A correlations and Panel C pair counts; both predate the June 2026 core-workbook update.",
            "- The affinity package README declares heterogeneous affinity/binding endpoint semantics but uses a uniform favorable-direction arrow, which is insufficient for model-ready label direction.",
            "",
            "## Sequence-Unique Finding",
            "",
            f"- Status: **{sequence_definition.get('status', 'manual_confirmation')}**",
            f"- Finding: {sequence_definition.get('definition', 'See the resource metric recalculation and identity audit.')}",
            f"- Evidence: {sequence_definition.get('evidence', 'Current sequence fields, duplicate profiles, and resource-count scripts.')}",
            "",
            "## Submission Recommendation",
            "",
            "Do not submit a frozen Scientific Data release until the blocking provenance, licensing, identity, and current-data/figure inconsistencies are resolved. "
            "Unlike the superseded audit conclusion, the current five authoritative assets are a viable base for a release; the remaining work is controlled reconciliation and packaging rather than recovery of an unknown v2.0 dataset.",
        ]
    )
    return report, matrix, requests, proposed_release_structure()


def manual_requests(
    sequence_definition: dict[str, str],
    dictionary_mismatch_count: int,
) -> list[dict[str, Any]]:
    questions = [
        (
            "critical",
            "resource count definition",
            "Approve the exact headline counting rule: canonical antibody_id union for DOTAD workbooks plus normalized affinity sequence fingerprints with heavy/light and fallback sequence representations, or provide a replacement strict sequence-unique definition.",
            "Four v2.0 workbooks; dotad_affinity_benchmark_v2.0.zip; resource metric script",
            "The current implementation is hybrid and does not justify an unqualified 'sequence-unique' label.",
            "BLOCKING",
            "corresponding author / sequence data curator",
        ),
        (
            "critical",
            "identity mapping",
            "Provide the source-ID to canonical-DOTAD-ID mapping and the adjudication rule for identical VH/VL pairs assigned to different antibody IDs or names.",
            "metadata/sequences workbook and source evidence tables",
            "Canonical identity, duplicate handling, and cross-table joins cannot be fully audited without this mapping.",
            "BLOCKING",
            "sequence data curator",
        ),
        (
            "critical",
            "provenance",
            "Provide row-level lineage fields linking each released value to source file, source sheet/row, PMID/DOI/dataset, curation action, and harmonization step.",
            "experimental, literature, and affinity evidence",
            "Scientific Data requires transparent record provenance.",
            "BLOCKING",
            "data curator",
        ),
        (
            "critical",
            "licensing",
            "For every literature and affinity source, state the licence/terms and whether redistribution of the included values and sequences is permitted.",
            "literature workbook and affinity benchmark ZIP",
            "Redistribution rights cannot be inferred from citation alone.",
            "BLOCKING",
            "corresponding author / institutional data steward",
        ),
        (
            "critical",
            "manuscript/figure version",
            "Choose the frozen data/analysis version for submission and regenerate or revise the metadata composition, assay coverage, Figure 4 cross-source, and neighbor-concordance numbers from that version.",
            "manuscript; Figure 2/4 outputs; current June 2026 core workbooks",
            "Current primary data and manuscript/figure counts are not aligned.",
            "BLOCKING",
            "lead analyst / figure analyst",
        ),
        (
            "high",
            "data dictionary",
            f"Resolve the {dictionary_mismatch_count} data-dictionary row/column or release-member count mismatches listed in data_dictionary_inventory_crosscheck.tsv.",
            "dotad_data_dictionary_v2.0.xlsx",
            "The dictionary should exactly describe the deposited files.",
            "MANUAL_CONFIRMATION",
            "data curator",
        ),
        (
            "high",
            "affinity endpoint direction",
            "Confirm per-dataset value transforms and favorable/risk directions for KD, IC50, EC50, binary binding, enrichment, and already transformed -log endpoints.",
            "dotad_affinity_benchmark_v2.0.zip::binding/README.md and all 86 datasets",
            "A single upward-favorable convention is not valid without endpoint-specific transforms.",
            "BLOCKING",
            "benchmark lead",
        ),
        (
            "high",
            "benchmark splits",
            "State whether the affinity v2.0 package is only a source bundle or a model-ready benchmark; if model-ready, provide deterministic split assignments, grouping constraints, seeds, and sequence-leakage checks.",
            "dotad_affinity_benchmark_v2.0.zip",
            "The current package contains datasets and README metadata but no split/leakage artefacts.",
            "MANUAL_CONFIRMATION",
            "benchmark lead",
        ),
        (
            "high",
            "pipeline repair",
            "Update the Fig3/Fig4 analysis to read the current `Field Dictionary`/`Assay Endpoint Dictionary` schema and make affinity metric recalculation read the v2.0 ZIP directly or document a deterministic extraction step.",
            "R/fig3_fig4_landscape_analysis.R; scripts/recalculate_homepage_metrics.py",
            "The existing scripts are not end-to-end runnable against the five authoritative assets as packaged.",
            "BLOCKING",
            "analysis pipeline maintainer",
        ),
        (
            "medium",
            "DOTAD 1.0 baseline",
            "Identify the immutable DOTAD 1.0 release files used for the 963-entry and 11,139-record comparison.",
            "historical manuscript claims",
            "Historical baseline numbers are outside the current five-file data scope.",
            "MANUAL_CONFIRMATION",
            "corresponding author",
        ),
        (
            "medium",
            "repository accession",
            "Specify the public repository, DOI/accession, immutable release version, and final file list for the Scientific Data deposition.",
            "Data availability statement and release manifest",
            "The website URL alone does not identify a frozen archival release.",
            "MANUAL_CONFIRMATION",
            "corresponding author",
        ),
    ]
    return [
        {
            "request_id": f"R{index:03d}",
            "priority": priority,
            "topic": topic,
            "question": question,
            "affected_files": affected,
            "why_required": reason,
            "blocking_status": blocking,
            "recommended_responder": responder,
        }
        for index, (
            priority,
            topic,
            question,
            affected,
            reason,
            blocking,
            responder,
        ) in enumerate(questions, start=1)
    ]


def proposed_release_structure() -> str:
    return """# Proposed DOTAD 2.0 Scientific Data Release Structure

```text
DOTAD2_scientific_data_release_v2.0.0/
├── README.md
├── CHANGELOG.md
├── CITATION.cff
├── LICENSES.tsv
├── checksums_sha256.txt
├── release_manifest.tsv
├── metadata/
│   ├── antibody_metadata.tsv
│   └── identity_mapping.tsv
├── sequences/
│   ├── antibody_sequences.tsv
│   └── sequence_qc.tsv
├── evidence/
│   ├── experimental_average.tsv
│   ├── experimental_raw.tsv
│   └── literature_curated.tsv
├── affinity/
│   ├── source_manifest.tsv
│   └── source_datasets/
├── harmonized/
│   ├── antibody_assay_values.tsv
│   └── source_aware_analysis_subset.tsv
├── provenance/
│   ├── source_manifest.tsv
│   ├── record_lineage.tsv
│   └── curation_audit.tsv
├── dictionaries/
│   ├── field_dictionary.tsv
│   ├── assay_endpoint_dictionary.tsv
│   └── controlled_vocabularies.tsv
├── benchmarks/
│   ├── task_manifest.tsv
│   ├── labels/
│   └── splits/
└── qc/
    ├── key_integrity.tsv
    ├── claim_reproduction.tsv
    └── release_validation_report.md
```

## Mapping Principles

| Suggested file | Existing source | Conversion | Row definition / key | Relationship | Redistribution risk | Author confirmation |
|---|---|---|---|---|---|---|
| `metadata/antibody_metadata.tsv` | `dotad_antibody_metadata_sequences_v2.0.xlsx::metadata` | XLSX to UTF-8 TSV | one current DOTAD antibody ID | parent for sequence/evidence | medium | identity scope |
| `metadata/identity_mapping.tsv` | not present in scoped assets | new export | one source/legacy/canonical ID relation | joins source identities | low | required |
| `sequences/antibody_sequences.tsv` | `dotad_antibody_metadata_sequences_v2.0.xlsx::sequences` | XLSX to TSV | one antibody ID sequence record | joins by antibody ID | high/variable | sequence rights and duplicate policy |
| `evidence/experimental_average.tsv` | average sheets in `dotad_experimental_developability_v2.0.xlsx` | sheet-preserving long export | one antibody/construct/endpoint statistic | source-aware lineage required | medium | aggregation semantics |
| `evidence/experimental_raw.tsv` | tidy sheets in `dotad_experimental_developability_v2.0.xlsx` | sheet-preserving long export | one source measurement | joins source and antibody IDs | medium | condition/replicate semantics |
| `evidence/literature_curated.tsv` | 29 study sheets in `dotad_literature_developability_collection_v2.0.xlsx` | union with explicit study/source columns | one curated source record | joins citation and antibody | high/variable | redistribution rights |
| `affinity/source_datasets/` | `dotad_affinity_benchmark_v2.0.zip` | preserve source files; normalize only in derived layer | source-specific | task/source manifest parent | high/variable | per-dataset licence and direction |
| `harmonized/antibody_assay_values.tsv` | derived from experimental/literature evidence | deterministic build | one antibody/endpoint/condition/statistic | row-level lineage required | medium | comparability rules |
| `provenance/record_lineage.tsv` | not present | new deterministic export | one released row lineage event | joins every release table | low | required |
| `dictionaries/field_dictionary.tsv` | `dotad_data_dictionary_v2.0.xlsx::Field Dictionary` | XLSX to TSV | one source field | describes all release columns | low | reconcile mismatches |

Do not overwrite the five authoritative source assets during release design. Freeze
them by checksum, then generate normalized deposition tables and QC artefacts into
a separate versioned release directory.
"""


def _action_for_status(status: str) -> str:
    return {
        "READY": "Retain and version-pin.",
        "REBUILDABLE": "Generate from final inputs with a committed script.",
        "MANUAL_CONFIRMATION": "Obtain author decision and document it.",
        "MISSING": "Create before deposition.",
        "BLOCKING": "Resolve before Scientific Data submission.",
        "NOT_NEEDED": "Exclude from deposition or move to website documentation.",
    }[status]
