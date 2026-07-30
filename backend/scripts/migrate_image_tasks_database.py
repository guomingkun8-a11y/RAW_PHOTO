from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import sys

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(ROOT_DIR / ".env.local", override=False)
load_dotenv(ROOT_DIR / ".env", override=False)

from services.database_migrations import run_migrations
from services.enterprise_schema import resolve_enterprise_database_url
from services.image_task_store import DatabaseImageTaskStore


def _sqlite_source_url(source_path: Path) -> str:
    return f"sqlite:///{source_path}"


def _vacuum_sqlite_database(source_path: Path) -> None:
    if not source_path.exists():
        return
    connection = sqlite3.connect(source_path)
    try:
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate image tasks from SQLite to MySQL and compact the old SQLite file.")
    parser.add_argument("--source-path", type=Path, default=ROOT_DIR / "data" / "image_tasks.db")
    parser.add_argument("--dest-url", default="", help="Destination SQLAlchemy database URL. Environment is preferred.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect counts without writing changes.")
    parser.add_argument("--skip-vacuum", action="store_true", help="Do not VACUUM the SQLite source after migration.")
    args = parser.parse_args()

    source_path = args.source_path.expanduser()
    source_url = _sqlite_source_url(source_path)
    dest_url = resolve_enterprise_database_url(args.dest_url or None)

    source_store = DatabaseImageTaskStore(source_url)
    dest_store = None
    try:
        source_tasks = source_store.load_all()
        source_count = len(source_tasks)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "source_path": str(source_path),
                        "source_count": source_count,
                        "destination_url": dest_url.split("@")[-1],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        run_migrations(dest_url)
        dest_store = DatabaseImageTaskStore(dest_url)
        existing_keys = list(dest_store.load_all().keys())
        if existing_keys:
            dest_store.delete_keys(existing_keys)
        if source_tasks:
            dest_store.save_all(source_tasks)
        dest_count = len(dest_store.load_all())
    finally:
        source_store.close()
        if dest_store is not None:
            dest_store.close()

    vacuum_status = "skipped"
    if not args.skip_vacuum:
        try:
            _vacuum_sqlite_database(source_path)
            vacuum_status = "ok"
        except Exception as exc:
            vacuum_status = f"failed: {exc}"

    print(
        json.dumps(
            {
                "dry_run": False,
                "source_path": str(source_path),
                "source_count": source_count,
                "destination_url": dest_url.split("@")[-1],
                "destination_count": dest_count,
                "vacuum": vacuum_status,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
