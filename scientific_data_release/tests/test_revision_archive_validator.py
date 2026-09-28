"""Public, synthetic tests: no authoritative data or reviewer material."""
import importlib.util
import io
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

path = Path(__file__).resolve().parents[2] / 'revision_tools' / 'validate_archive.py'
spec = importlib.util.spec_from_file_location('revision_validator', path)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def make_zip(names):
    output = io.BytesIO()
    with ZipFile(output, 'w') as z:
        for name, value in names:
            info = ZipInfo()
            # Do not let Windows normalize the malicious test member before validation.
            info.filename = name
            z.writestr(info, value)
    return output.getvalue()


def test_safe_root_and_utf8_table():
    data = b'id\tvalue\n1\t4.006022\n'
    assert validator.unzip(make_zip([('root/a.tsv', data)])) == {'a.tsv': data}
    assert validator.table(data) == [{'id': '1', 'value': '4.006022'}]


@pytest.mark.parametrize('name', ['../bad', '/bad', 'root/../bad', 'root\\bad', 'root/a:b'])
def test_unsafe_zip_paths_rejected(name):
    with pytest.raises(AssertionError):
        validator.unzip(make_zip([(name, b'x')]), False)


def test_case_collision_rejected():
    with pytest.raises(AssertionError):
        validator.unzip(make_zip([('root/a', b'x'), ('root/A', b'y')]))


def test_digest_detects_changed_payload():
    assert validator.sha(b'4.006022') != validator.sha(b'4.006023')
