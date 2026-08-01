from __future__ import annotations
import hashlib
import re
import shutil
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def deterministic_zip(source_root: Path, output_zip: Path) -> Path:
    source_root = Path(source_root)
    output_zip = Path(output_zip)
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_zip, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source_root.rglob('*')):
            if not path.is_file():
                continue
            info = ZipInfo(path.relative_to(source_root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    return output_zip

def finalize_publication(prepublish_root: Path, output_root: Path, doi: str, release_date: str) -> Path:
    if not re.fullmatch(r'10\.\d{4,9}/[^\s]+', doi):
        raise ValueError('Invalid DOI format')
    try:
        if date.fromisoformat(release_date).isoformat() != release_date:
            raise ValueError
    except ValueError as exc:
        raise ValueError('Invalid release date') from exc
    source, output = Path(prepublish_root), Path(output_root)
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(source, output, copy_function=shutil.copyfile)
    for path in output.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.md', '.txt', '.cff', '.json'}:
            text = path.read_text(encoding='utf-8')
            text = text.replace('__DATA_DOI__', doi).replace('v2.0.0-prepublish', 'v2.0.0')
            path.write_text(text, encoding='utf-8', newline='\n')
    return output
