from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotad_audit.verify_outputs import (
    EXPECTED_AUTHORITATIVE_RELATIVE_PATHS,
    immutable_hashes_match,
    sha256_file,
    stale_required_outputs,
)


def test_sha256_file_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(b"DOTAD 2.0 audit fixture")

    first = sha256_file(path)
    second = sha256_file(path)

    assert first == second
    assert len(first) == 64


def test_expected_authoritative_scope_is_exactly_five_current_assets() -> None:
    assert EXPECTED_AUTHORITATIVE_RELATIVE_PATHS == {
        "assets/data/dotad_antibody_metadata_sequences_v2.0.xlsx",
        "assets/data/dotad_data_dictionary_v2.0.xlsx",
        "assets/data/dotad_experimental_developability_v2.0.xlsx",
        "assets/data/dotad_literature_developability_collection_v2.0.xlsx",
        "assets/data/dotad_affinity_benchmark_v2.0.zip",
    }


def test_immutable_hashes_reject_changed_before_hash() -> None:
    record = {
        "sha256_before": "before",
        "sha256_after": "after",
        "unchanged": True,
    }

    assert not immutable_hashes_match(record, "after")


def test_stale_required_outputs_detects_prior_run_file(tmp_path: Path) -> None:
    output = tmp_path / "report.tsv"
    output.write_text("old", encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    os.utime(output, (old.timestamp(), old.timestamp()))

    stale = stale_required_outputs(
        tmp_path,
        datetime.now(timezone.utc),
        ("report.tsv",),
    )

    assert stale == ["report.tsv"]
