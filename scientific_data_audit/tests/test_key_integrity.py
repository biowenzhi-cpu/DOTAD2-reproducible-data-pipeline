from dotad_audit.key_integrity import audit_foreign_key, audit_key_column


def test_candidate_primary_key_uniqueness():
    records = [{"DOTAD_ID": "DOTAD-1"}, {"DOTAD_ID": "DOTAD-2"}]
    result = audit_key_column("T1", records, "DOTAD_ID")
    assert result["candidate_key_status"] == "confirmed_candidate"
    assert result["duplicate_count"] == 0


def test_foreign_key_orphans_are_detected():
    parent = [{"DOTAD_ID": "DOTAD-1"}, {"DOTAD_ID": "DOTAD-2"}]
    child = [
        {"antibody_id": "DOTAD-1"},
        {"antibody_id": "dotad-3"},
        {"antibody_id": ""},
    ]
    result, orphans = audit_foreign_key(
        "child", "antibody_id", child, "parent", "DOTAD_ID", parent
    )
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 1
    assert orphans[0]["orphan_identifier"] == "DOTAD-3"
