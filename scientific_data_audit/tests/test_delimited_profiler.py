from dotad_audit.delimited_profiler import (
    detect_encoding_and_delimiter,
    iter_delimited_chunks,
)


def test_encoding_and_delimiter_detection():
    sample = "id\t名称\tvalue\nA\t抗体\t1\n".encode("utf-8")
    encoding, delimiter = detect_encoding_and_delimiter(sample, ".tsv")
    assert encoding.lower().replace("_", "-") in {"utf-8", "utf-8-sig"}
    assert delimiter == "\t"


def test_large_file_is_read_in_chunks(tmp_path):
    path = tmp_path / "large.csv"
    with path.open("w", encoding="utf-8") as handle:
        handle.write("id,value\n")
        for index in range(10_005):
            handle.write(f"{index},{index * 2}\n")
    chunks = list(iter_delimited_chunks(path, chunksize=1_000))
    assert len(chunks) == 11
    assert sum(len(chunk) for chunk in chunks) == 10_005
    assert len(chunks[0]) == 1_000
