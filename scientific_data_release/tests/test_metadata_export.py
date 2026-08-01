from __future__ import annotations

import csv
from pathlib import Path

import openpyxl

from dotad_release.metadata_export import export_metadata
from dotad_release.models import BuildContext
from dotad_release.workbook_reader import sha256_file


METADATA_HEADERS = (
    "antibody_id",
    "antibody_name",
    "Format",
    "hc_subtype",
    "lc_subtype",
    "Highest_Clin_Trial",
    "est_status_asof_feb2025",
    "Target",
    "Species",
    "Clinical indication",
    "PDB",
    "PDB_reference",
    "ADAs_rate",
    "ADAs_reference",
    "production_host",
    "tag",
)


def _metadata_workbook(path: Path) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "metadata"
    sheet.append(METADATA_HEADERS)
    sheet.append(
        [
            "DOTAD-001",
            "adalimumab",
            "Whole mAb",
            "G1",
            "Kappa",
            "Approved",
            "Active",
            "TNF",
            "Human",
            "Inflammation",
            "\t1abc",
            "https://example.test/1abc",
            12.5,
            "PMID:1",
            None,
            "source-a",
        ]
    )
    sheet.append([None] * len(METADATA_HEADERS))
    sheet.append(
        [
            "DOTAD-001",
            "Humira",
            "Fab",
            None,
            None,
            "Approved",
            "Active",
            "TNF",
            "Human",
            None,
            None,
            None,
            None,
            None,
            None,
            "duplicate-id",
        ]
    )
    sheet.append(
        [
            "DOTAD-002",
            "  B\u00e9ta  ",
            "scFv",
            "G4",
            "Lambda",
            "Phase-I",
            "Active",
            "X",
            "Chimeric",
            "",
            None,
            None,
            0,
            "",
            "CHO",
            None,
        ]
    )
    sheet.append(
        [
            None,
            "orphan source name",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
    )
    sequences = workbook.create_sheet("sequences")
    sequences.append(["antibody_id", "antibody_name", "VH", "VL"])
    workbook.save(path)
    return path


def _context(tmp_path: Path, source: Path) -> BuildContext:
    return BuildContext(
        output_dir=tmp_path / "release",
        core_inputs={"metadata_sequences": source},
        sheet_roles={
            "metadata_sequences": {
                "metadata": "metadata",
                "sequences": "sequences",
            }
        },
        input_shas={"metadata_sequences": sha256_file(source)},
        release_version="2.0.0-draft",
        script_root=tmp_path,
    )


def test_metadata_export_preserves_every_source_row_column_and_value(
    tmp_path: Path,
) -> None:
    source = _metadata_workbook(tmp_path / "metadata_sequences.xlsx")

    result = export_metadata(_context(tmp_path, source))

    assert result.fieldnames[: len(METADATA_HEADERS)] == METADATA_HEADERS
    assert len(result.rows) == 4
    assert [row["record_id"] for row in result.rows] == [
        "META:metadata:2",
        "META:metadata:4",
        "META:metadata:5",
        "META:metadata:6",
    ]
    assert result.rows[0]["metadata_record_id"] == "META:metadata:2"
    assert result.rows[0]["input_file"] == source.name
    assert result.rows[0]["input_file_sha256"] == sha256_file(source)
    assert result.rows[0]["PDB"] == "\t1abc"
    assert result.rows[0]["ADAs_rate"] == 12.5
    assert result.rows[0]["production_host"] is None
    assert result.rows[2]["antibody_name"] == "  B\u00e9ta  "
    assert result.rows[3]["antibody_id"] is None
    assert result.rows[0]["source_excel_row"] == 2
    assert result.rows[0]["source_input_sha256"] == sha256_file(source)
    assert result.rows[0]["release_version"] == "2.0.0-draft"
    assert result.rows[0]["transformation_rule"] == "source_row_preserved"
    assert result.row_definition == "one non-empty source row from metadata"

    with result.path.open(encoding="utf-8", newline="") as handle:
        written = list(csv.DictReader(handle, delimiter="\t"))
    assert tuple(written[0]) == result.fieldnames
    assert written[0]["PDB"] == "\t1abc"
    assert written[0]["ADAs_rate"] == "12.5"


def test_metadata_record_count_is_not_an_antibody_identity_count(
    tmp_path: Path,
) -> None:
    source = _metadata_workbook(tmp_path / "metadata_sequences.xlsx")

    result = export_metadata(_context(tmp_path, source))

    non_missing_ids = [
        row["antibody_id"] for row in result.rows if row["antibody_id"]
    ]
    unique_ids = set(non_missing_ids)
    duplicate_id_occurrences = len(non_missing_ids) - len(unique_ids)
    assert len(result.rows) == 4
    assert len(non_missing_ids) == 3
    assert len(unique_ids) == 2
    assert duplicate_id_occurrences == 1
    assert len(result.rows) not in {
        len(non_missing_ids),
        len(unique_ids),
        duplicate_id_occurrences,
    }


def test_metadata_aliases_are_source_occurrences_not_canonical_names(
    tmp_path: Path,
) -> None:
    source = _metadata_workbook(tmp_path / "metadata_sequences.xlsx")

    export_metadata(_context(tmp_path, source))

    alias_path = (
        tmp_path / "release" / "metadata" / "antibody_aliases.tsv"
    )
    with alias_path.open(encoding="utf-8", newline="") as handle:
        aliases = list(csv.DictReader(handle, delimiter="\t"))
    assert len(aliases) == 4
    assert all(row["alias_id"].startswith("ALIAS:") for row in aliases)
    assert [row["alias"] for row in aliases] == [
        "adalimumab",
        "Humira",
        "  B\u00e9ta  ",
        "orphan source name",
    ]
    assert aliases[2]["normalized_alias"] == "b\u00e9ta"
    assert all(row["alias_role"] == "source_name_occurrence" for row in aliases)
    assert all(row["canonical_name"] == "" for row in aliases)
