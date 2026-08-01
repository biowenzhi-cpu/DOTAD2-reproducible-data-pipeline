from dotad_release.ids import stable_content_id, stable_source_record_id


def test_source_record_id_uses_nfkc_sheet_and_excel_row() -> None:
    assert (
        stable_source_record_id("EXP", "Assay Data - average（3）", 2)
        == "EXP:assay_data_average_3:2"
    )


def test_content_id_is_stable_and_order_sensitive() -> None:
    left = stable_content_id("ALIAS", ["DOTAD-001", "adalimumab"])
    right = stable_content_id("ALIAS", ["DOTAD-001", "adalimumab"])
    reversed_id = stable_content_id("ALIAS", ["adalimumab", "DOTAD-001"])
    assert left == right
    assert left != reversed_id
    assert left.startswith("ALIAS:")
