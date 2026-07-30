# Enterprise Image Schema

The phase-two schema is additive. It does not replace the legacy `image_tasks` table yet, so current image generation behavior remains unchanged until the task-store refactor phase.

## Tables

- `image_task_batches`: one user submission, including requested and completed counts.
- `image_task_items`: one durable execution unit per generated image.
- `image_task_events`: append-only status and error history.
- `image_assets`: object-storage metadata for references, results, and thumbnails.
- `usage_ledger`: idempotent quota reservation, consumption, refund, and adjustment entries.
- `upstream_accounts`: provider capacity, health, rate-limit windows, and secret-manager references.

Critical query fields are normalized columns. Flexible request and result details use MySQL JSON columns. Provider credentials are never stored in these tables; `credential_ref` points to an external secret manager entry.

## Initialization

```bash
GMKRAW_DATABASE_URL='mysql+pymysql://user:password@mysql:3306/raw_photo?charset=utf8mb4' \
uv run python backend/scripts/init_enterprise_schema.py
```

The command uses SQLAlchemy `create_all` semantics and can be run repeatedly. Future destructive or data-transforming changes should use versioned Alembic migrations.

## Next phase

The current in-memory/full-snapshot task store remains active. The next storage phase should move submission, claiming, status transitions, cancellation, and retries to row-level transactions on these tables.
