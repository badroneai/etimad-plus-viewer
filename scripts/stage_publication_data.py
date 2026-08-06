from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


def validate_publication_tree(data_root: Path) -> None:
    if not data_root.is_dir() or data_root.is_symlink():
        raise ValueError(f"publication data directory is missing or unsafe: {data_root}")
    if not (data_root / "manifest.json").is_file():
        raise ValueError("publication data is missing manifest.json")

    for directory, names, files in os.walk(data_root, followlinks=False):
        current = Path(directory)
        for name in names:
            path = current / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f"publication data contains an unsafe directory: {path}")
        for name in files:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"publication data contains an unsafe file: {path}")


def stage_publication_data(publication_root: Path, destination: Path) -> None:
    source = publication_root / "data"
    validate_publication_tree(source)

    destination_parent = destination.parent
    destination_parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=".kashaf-publication-", dir=destination_parent)
    )
    staged = temporary_root / "data"
    backup = temporary_root / "previous-data"

    try:
        shutil.copytree(source, staged, copy_function=shutil.copy2)
        validate_publication_tree(staged)
        if destination.exists():
            destination.replace(backup)
        staged.replace(destination)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely overlay immutable publication data onto the Pages source tree."
    )
    parser.add_argument("--publication-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    stage_publication_data(args.publication_root, args.destination)


if __name__ == "__main__":
    main()
