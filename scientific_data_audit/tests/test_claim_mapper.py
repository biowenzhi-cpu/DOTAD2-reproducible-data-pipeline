import pytest

from dotad_audit.claim_mapper import map_claims, validate_mapping_row


def test_claim_mapping_status_logic():
    claims = [
        {
            "claim_id": "c1",
            "claim_text": "There are 10 entries.",
            "metric_name": "entries",
            "claim_category": "resource_size",
            "claimed_value": 10,
        }
    ]
    facts = {
        "c1": {
            "candidate_source_file": "data.tsv",
            "candidate_sheet_or_table": "data",
            "candidate_columns": "id",
            "candidate_filter_logic": "count rows",
            "candidate_script": "audit.py",
            "reproduction_command": "python audit.py",
            "recomputed_value": 10,
            "mapping_status": "exact_file_and_script",
            "verification_status": "verified_exact",
            "confidence": "high",
        }
    }
    result = map_claims(claims, [], [], [], facts)[0]
    assert result["verification_status"] == "verified_exact"
    assert result["difference_absolute"] == 0

    invalid = dict(result)
    invalid["reproduction_command"] = ""
    with pytest.raises(ValueError):
        validate_mapping_row(invalid)
