from __future__ import annotations
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'synthetic_release.tsv').write_text(
        'synthetic_id\tendpoint\tvalue\nSYNTH-001\tassay_a\t1.0\n',
        encoding='utf-8', newline='\n')

if __name__ == '__main__':
    main()
