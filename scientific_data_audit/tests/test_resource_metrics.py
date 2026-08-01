from dotad_audit.resource_metrics import sequence_fingerprint


def test_sequence_fingerprint_prefers_ordered_heavy_light_pair():
    headers = ["VH", "VL", "fitness"]
    row = ["EVQLVESGGGLVQPGGSLRLS", "DIQMTQSPSSLSASVGDRVT", "1.0"]

    fingerprint = sequence_fingerprint(headers, row, [0, 1])

    assert fingerprint == (
        "heavy_light",
        "EVQLVESGGGLVQPGGSLRLS",
        "DIQMTQSPSSLSASVGDRVT",
    )


def test_sequence_fingerprint_is_stable_after_case_and_spacing_normalization():
    headers = ["heavy", "light"]
    first = sequence_fingerprint(
        headers,
        ["evql vesggglvq pggslrls", "diqmtqspsslsasvgdrvt"],
        [0, 1],
    )
    second = sequence_fingerprint(
        headers,
        ["EVQLVESGGGLVQPGGSLRLS", "DIQMTQSPSSLSASVGDRVT"],
        [0, 1],
    )

    assert first == second
