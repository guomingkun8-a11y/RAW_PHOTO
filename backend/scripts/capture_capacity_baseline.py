from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.config import config
from services.enterprise_schema import resolve_enterprise_database_url


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _generation_metrics(engine) -> dict[str, object]:
    if "generation_task_events" not in inspect(engine).get_table_names():
        return {"available": False, "reason": "generation_task_events table is missing"}

    with engine.connect() as connection:
        rows = list(
            connection.execute(
                text(
                    "SELECT status, duration_ms, error FROM generation_task_events "
                    "ORDER BY id DESC LIMIT 10000"
                )
            ).mappings()
        )

    status_counts: dict[str, int] = {}
    durations: list[int] = []
    rate_limited = 0
    for row in rows:
        status = str(row.get("status") or "unknown").strip().lower()
        status_counts[status] = status_counts.get(status, 0) + 1
        duration = row.get("duration_ms")
        if isinstance(duration, int) and duration >= 0:
            durations.append(duration)
        error = str(row.get("error") or "").lower()
        if "rate_limit" in error or "429" in error or "限流" in error:
            rate_limited += 1

    successful = sum(
        count for status, count in status_counts.items() if status in {"success", "succeeded", "completed", "done"}
    )
    failed = sum(count for status, count in status_counts.items() if status in {"error", "failed"})
    total = len(rows)
    return {
        "available": True,
        "sample_size": total,
        "status_counts": status_counts,
        "success_rate": round(successful / total, 4) if total else None,
        "failure_rate": round(failed / total, 4) if total else None,
        "rate_limited_count": rate_limited,
        "duration_ms": {
            "average": round(sum(durations) / len(durations)) if durations else None,
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "maximum": max(durations) if durations else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a redacted capacity baseline for the current deployment.")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "data" / "capacity-baseline.json")
    args = parser.parse_args()

    database_url = resolve_enterprise_database_url(args.database_url or None)
    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
    try:
        payload = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "application_version": config.app_version,
            "configured_image_concurrency": config.image_account_concurrency,
            "image_parallel_generation": config.image_parallel_generation,
            "task_queue": config.get_public_image_task_queue_settings(),
            "generation_metrics": _generation_metrics(engine),
        }
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"capacity baseline written to {args.output}")


if __name__ == "__main__":
    main()
