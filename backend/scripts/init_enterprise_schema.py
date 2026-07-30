from __future__ import annotations

import argparse
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.database_migrations import run_migrations
from services.enterprise_schema import resolve_enterprise_database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify the enterprise MySQL schema.")
    parser.add_argument("--database-url", default="", help="SQLAlchemy database URL. Environment is preferred.")
    args = parser.parse_args()

    database_url = resolve_enterprise_database_url(args.database_url or None)
    result = run_migrations(database_url)
    print(f"database migrations ready: {len(result.get('applied', []))} versions")
    for version in result.get("applied_now", []):
        print(f"- applied {version}")


if __name__ == "__main__":
    main()
