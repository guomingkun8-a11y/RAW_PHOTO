from __future__ import annotations

import argparse
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.platform.database_migrations import run_migrations
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.platform.runtime_requirements import validate_enterprise_runtime
from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service
from services.ecommerce.professional_knowledge_service import sync_professional_knowledge_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify the enterprise MySQL schema.")
    parser.add_argument("--database-url", default="", help="SQLAlchemy database URL. Environment is preferred.")
    args = parser.parse_args()

    runtime = validate_enterprise_runtime()
    database_url = resolve_enterprise_database_url(args.database_url or None)
    result = run_migrations(database_url)
    print(f"database migrations ready: {len(result.get('applied', []))} versions")
    for version in result.get("applied_now", []):
        print(f"- applied {version}")
    knowledge = sync_professional_knowledge_index()
    memory = ecommerce_agent_memory_service.backfill_long_term_vectors(limit=100000)
    print(
        "professional vectors ready: "
        f"knowledge={knowledge['vectorReady']}/{knowledge['chunks']}, "
        f"memory={memory['indexed']}/{memory['rows']}"
    )
    if runtime.get("vectorSearchRequired") and (knowledge["vectorPending"] or memory["pending"] or memory["failed"]):
        raise RuntimeError(
            "enterprise vector backfill is incomplete; verify the embedding endpoint and Qdrant before startup"
        )


if __name__ == "__main__":
    main()
