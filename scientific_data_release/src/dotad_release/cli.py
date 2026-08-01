from __future__ import annotations
import argparse
from pathlib import Path
from .public_release import finalize_publication

def main(argv=None):
    parser = argparse.ArgumentParser(prog='dotad-release')
    sub = parser.add_subparsers(dest='command', required=True)
    cmd = sub.add_parser('finalize-publication')
    cmd.add_argument('--prepublish-root', type=Path, required=True)
    cmd.add_argument('--output-root', type=Path, required=True)
    cmd.add_argument('--data-doi', required=True)
    cmd.add_argument('--release-date', required=True)
    args = parser.parse_args(argv)
    finalize_publication(args.prepublish_root, args.output_root, args.data_doi, args.release_date)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
