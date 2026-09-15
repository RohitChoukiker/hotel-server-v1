"""Validate India folder mappings without mutating source files."""

import argparse
import json
from pathlib import Path


def main() -> int:
    """Report mapped and unmapped source folders."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path("data/mappings/india_folder_regions.json"),
    )
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    folders = sorted(item.name for item in args.source_root.iterdir() if item.is_dir())
    unmapped = [name for name in folders if name not in mapping]
    for name in folders:
        print(f"{name}: {mapping.get(name, 'UNMAPPED')}")
    return 1 if unmapped else 0


if __name__ == "__main__":
    raise SystemExit(main())

