from __future__ import annotations

import argparse
import json
from pathlib import Path

from .firebase_store import export_rows_to_firebase
from .main import Base, firebase_store


def import_table_export(path: Path, *, replace_tables: bool = False) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The export must be a JSON object keyed by table name.")
    export_rows_to_firebase(
        firebase_store(),
        data,
        Base,
        replace_tables=replace_tables,
    )
    return [name for name in data if name != "_meta"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a JSON table export into Firebase Realtime Database."
    )
    parser.add_argument("input", type=Path, help="JSON object containing table rows")
    parser.add_argument(
        "--replace-tables",
        action="store_true",
        help="Replace each imported table node instead of merging its rows",
    )
    args = parser.parse_args()
    tables = import_table_export(args.input.resolve(), replace_tables=args.replace_tables)
    print(f"Imported {len(tables)} tables into Firebase Realtime Database.")


if __name__ == "__main__":
    main()
