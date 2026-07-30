from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.config import config
from services.database_migrations import migration_status, run_migrations
from services.enterprise_schema import resolve_enterprise_database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Run idempotent application database migrations.")
    parser.add_argument("--database-url", default="", help="SQLAlchemy database URL. Environment is preferred.")
    parser.add_argument("--dry-run", action="store_true", help="Only print pending migration versions.")
    parser.add_argument("--status", action="store_true", help="Print migration status without applying changes.")
    args = parser.parse_args()

    database_url = resolve_enterprise_database_url(args.database_url or None)
    result = migration_status(database_url) if args.status else run_migrations(database_url, dry_run=args.dry_run)
    print(json.dumps({**result, "application_version": config.app_version}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
