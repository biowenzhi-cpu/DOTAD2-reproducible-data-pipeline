from dotad_release.workbook_reader import iter_source_rows


def test_reader_skips_blank_rows_but_retains_physical_excel_row(
    workbook_with_header_blank_and_record,
) -> None:
    rows = list(iter_source_rows(workbook_with_header_blank_and_record, "metadata"))
    assert [row.excel_row for row in rows] == [3]
    assert [item.header for item in rows[0].payload] == [
        "antibody_id",
        "name",
        "",
        "name",
    ]
    assert rows[0].payload[2].value == "blank-header-value"
