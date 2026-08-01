from dotad_audit.sequence_profiler import (
    invalid_characters,
    normalize_sequence_for_audit,
    vh_vl_pair_hash,
)


def test_amino_acid_invalid_characters():
    assert invalid_characters("ACD-EF1*", "amino_acid") == ["-", "1"]
    assert invalid_characters("ACDEFGHIKLMNPQRSTVWY", "amino_acid") == []


def test_vh_vl_pair_hash_is_stable_under_audit_normalization():
    first = vh_vl_pair_hash(" acd ef* ", "GGH\nII")
    second = vh_vl_pair_hash("ACDEF", "gghii")
    reversed_pair = vh_vl_pair_hash("GGHII", "ACDEF")
    assert first == second
    assert first != reversed_pair
    assert normalize_sequence_for_audit(" acd*\n") == "ACD"
