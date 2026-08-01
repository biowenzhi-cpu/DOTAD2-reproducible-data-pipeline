from __future__ import annotations

from datetime import date, datetime

from dotad_release.workbook_reader import serialize_scalar, sha256_file, write_tsv


def test_tsv_output_is_utf8_lf_and_stable(tmp_path) -> None:
    rows = [
        {"id": "α", "number": 1.25, "day": date(2026, 7, 30), "empty": None},
        {
            "id": "b",
            "number": 2,
            "day": datetime(2026, 7, 30, 12, 30),
            "empty": "",
        },
    ]
    first = tmp_path / "first.tsv"
    second = tmp_path / "second.tsv"
    fields = ["id", "number", "day", "empty"]
    write_tsv(first, rows, fields)
    write_tsv(second, rows, fields)
    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()
    assert sha256_file(first) == sha256_file(second)


def test_scalar_serialization_does_not_emit_python_null_tokens() -> None:
    assert serialize_scalar(None) == ""
    assert serialize_scalar(True) == "true"
    assert serialize_scalar(False) == "false"
