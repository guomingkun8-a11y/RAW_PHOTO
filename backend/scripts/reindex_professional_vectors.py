from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service
from services.ecommerce.professional_knowledge_service import sync_professional_knowledge_index
from services.platform.database_migrations import run_migrations
from services.platform.enterprise_schema import resolve_enterprise_database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild RAW professional knowledge and memory vector indexes.")
    parser.add_argument("--owner-id", default="", help="Optional owner filter for long-term memory backfill.")
    parser.add_argument("--limit", type=int, default=10000, help="Maximum long-term memory rows to process.")
    args = parser.parse_args()

    database_url = resolve_enterprise_database_url()
    migrations = run_migrations(database_url)
    knowledge = sync_professional_knowledge_index()
    memory = ecommerce_agent_memory_service.backfill_long_term_vectors(
        owner_id=args.owner_id,
        limit=args.limit,
    )
    print(json.dumps({
        "migrations": {"appliedNow": migrations.get("applied_now", [])},
        "knowledge": knowledge,
        "memory": memory,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
