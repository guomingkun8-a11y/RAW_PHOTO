from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.config import config
from services.database_migrations import run_migrations
from services.enterprise_schema import resolve_enterprise_database_url
from services.image_task_assets import contains_inline_assets, normalize_task_result, prepare_task_payload
from services.image_task_store import DatabaseImageTaskStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Move Base64 image task assets to configured object storage.")
    parser.add_argument("--database-url", default="", help="SQLAlchemy database URL. Environment is preferred.")
    parser.add_argument("--base-url", default="", help="Public API base URL for stored task result URLs.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of tasks to inspect; 0 means all.")
    parser.add_argument("--dry-run", action="store_true", help="Count tasks without writing storage or database changes.")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent task asset upload workers.")
    parser.add_argument("--only-legacy", action="store_true", help="Inspect only tasks that still contain inline image inputs.")
    args = parser.parse_args()

    database_url = resolve_enterprise_database_url(args.database_url or None)
    if not args.dry_run:
        run_migrations(database_url)
    store = DatabaseImageTaskStore(database_url)
    inspected = changed = payload_assets = result_assets = 0
    try:
        candidates = list(store.load_all().items())
        if args.only_legacy:
            candidates = [item for item in candidates if contains_inline_assets(item[1].get("payload"))]
        if args.limit > 0:
            candidates = candidates[:args.limit]

        def migrate_one(item: tuple[str, dict[str, object]]) -> tuple[str, dict[str, object] | None, bool, bool, str]:
            key, task = item
            owner_id = str(task.get("owner_id") or "anonymous")
            task_id = str(task.get("id") or key.rsplit(":", 1)[-1])
            old_payload = task.get("payload")
            old_data = task.get("data")
            if args.dry_run:
                new_payload = old_payload
                new_data = old_data
                payload_changed = contains_inline_assets(old_payload)
                result_changed = any(
                    isinstance(item, dict) and bool(item.get("b64_json"))
                    for item in (old_data if isinstance(old_data, list) else [])
                )
            else:
                new_payload = (
                    prepare_task_payload(old_payload, owner_id=owner_id, task_id=task_id)
                    if isinstance(old_payload, dict)
                    else old_payload
                )
                new_data = (
                    normalize_task_result(
                        old_data,
                        owner_id=owner_id,
                        task_id=task_id,
                        base_url=args.base_url or config.base_url,
                    )
                    if isinstance(old_data, list)
                    else old_data
                )
                payload_changed = new_payload != old_payload
                result_changed = new_data != old_data
            if payload_changed:
                payload_count = True
            else:
                payload_count = False
            if result_changed:
                result_count = True
            else:
                result_count = False
            if not args.dry_run and (payload_changed or result_changed):
                updated = dict(task)
                if payload_changed:
                    updated["payload"] = new_payload
                if result_changed:
                    updated["data"] = new_data
                updated["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return key, updated, payload_count, result_count, ""
            return key, None, payload_count, result_count, ""

        workers = max(1, min(8, int(args.workers or 1)))
        failed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(migrate_one, item) for item in candidates]
            for future in as_completed(futures):
                try:
                    key, updated, payload_count, result_count, error = future.result()
                except Exception as exc:
                    failed += 1
                    inspected += 1
                    print(f"processed={inspected}/{len(candidates)} failed=1 error={exc}", flush=True)
                    continue
                inspected += 1
                if payload_count:
                    payload_assets += 1
                if result_count:
                    result_assets += 1
                if updated is not None:
                    store.save_task(key, updated)
                    changed += 1
                print(
                    f"processed={inspected}/{len(candidates)} changed={changed} "
                    f"payload_tasks={payload_assets} result_tasks={result_assets} failed={failed}",
                    flush=True,
                )
    finally:
        store.close()

    print(
        f"image task asset migration complete: inspected={inspected}, "
        f"changed={changed}, payload_assets={payload_assets}, result_assets={result_assets}, failed={failed}, "
        f"dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
