# Image Task Queue

This project can run image generation in an enterprise-safe queue mode:

- API process: validates requests, writes task status to the database, and enqueues task keys.
- Worker process: pulls task keys from Redis, loads payloads from the database, runs generation, retries failures, and writes final results.
- Redis: lightweight queue transport.
- Database: durable task state shared by API and workers.

## Local Docker

```bash
docker compose -f docker-compose.local.yml up --build
```

The local compose file starts:

- `app`: FastAPI + static web app
- `worker`: image task worker
- `redis`: queue backend

Local task state defaults to:

```text
sqlite:////app/data/image_tasks.db
```

## Enterprise Environment

Use MySQL or PostgreSQL for task state:

```bash
IMAGE_TASK_QUEUE_ENABLED=true
IMAGE_TASK_EXECUTOR=celery
IMAGE_TASK_REDIS_URL=redis://redis:6379/0
IMAGE_TASK_DATABASE_URL=mysql+pymysql://user:password@mysql:3306/raw_photo?charset=utf8mb4
IMAGE_TASK_DB_POOL_SIZE=10
IMAGE_TASK_DB_MAX_OVERFLOW=20
IMAGE_TASK_MAX_RETRIES=2
IMAGE_TASK_TOTAL_CONCURRENCY=8
IMAGE_TASK_WORKER_CONCURRENCY=3
IMAGE_TASK_OWNER_CONCURRENCY=2
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_ENABLED=false
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_THRESHOLD=10
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_MAX=20
IMAGE_TASK_OWNER_PENDING_LIMIT=50
```

Run the versioned database migration before starting API replicas:

```bash
uv run python backend/scripts/migrate_database.py
```

The migration command is idempotent and records versions in `schema_migrations`. It creates the enterprise tables, task tables, batch columns, and indexes. `backend/scripts/init_enterprise_schema.py` remains as a compatibility wrapper.

To move the existing local SQLite task store into MySQL and shrink the old SQLite file:

```bash
uv run python backend/scripts/migrate_image_tasks_database.py
```

The migration copies all current image tasks into MySQL, replaces existing rows in the destination table, and VACUUMs the old SQLite file after a successful copy.

Move legacy Base64 task inputs into the configured object storage backend after storage credentials are available:

```bash
uv run python backend/scripts/migrate_image_task_assets.py --dry-run
uv run python backend/scripts/migrate_image_task_assets.py
```

The dry run only counts legacy assets. The write command stores task inputs and inline Base64 results as object references, then updates task JSON. It does not delete the original local image files. For Aliyun OSS, set `GMKRAW_MINIO_ROOT_PATH` for generated task assets and `GMKRAW_OSS_REFERENCE_PREFIX` for public reference-image uploads.

Capture the current throughput and reliability baseline before changing worker concurrency:

```bash
uv run python backend/scripts/capture_capacity_baseline.py
```

Run the mock capacity test before enabling real upstream traffic:

```bash
uv run python backend/scripts/run_image_task_load_test.py --users 60 --api-instances 4 --workers 4 --total-concurrency 4 --owner-concurrency 2
```

The load test uses a fake image handler and reports submission throughput, completed tasks, duplicate idempotency results, peak queue depth, slot utilization, worker utilization, single-owner queue pressure, failure rate, and tuning recommendations. It never calls the upstream image API.

For a ready intranet rollout guide, startup scripts, tuning rules, and alert thresholds, see `docs/intranet-deployment.md`.

Run at least one API process and one worker process. Scale workers horizontally when generation demand grows.

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 80
uv run python worker.py
```

Only Worker processes perform startup recovery and requeue unfinished database tasks. API replicas only read and write task rows, so restarting or scaling the API does not duplicate recovery enqueues.

With `IMAGE_TASK_EXECUTOR=celery`, `worker.py` delegates to a Celery worker with late acknowledgements, one-task prefetch, worker-loss rejection, and Redis visibility recovery. With the default `redis` executor, the existing lightweight worker loop remains available for local development.

For a local enterprise-shaped stack with MySQL, password-protected Redis, and Aliyun OSS object storage:

```bash
docker compose --env-file .env.local -f docker-compose.enterprise.yml up --build
```

Create deployment secrets from `.env.example` or a deployment-only env file. Docker Compose does not load `.env.local` automatically; pass it explicitly with `--env-file` or export the variables before starting the stack. Do not reuse local development credentials in production.

## Behavior

- Duplicate `client_task_id` submissions return the existing task.
- Canceled queued tasks are skipped by workers.
- Canceled running tasks may finish upstream, but late success is ignored locally.
- Failed queued tasks are retried until `IMAGE_TASK_MAX_RETRIES` is exceeded.
- On queue-mode restart, unfinished tasks are requeued instead of being marked failed.
- Task payloads in the shared database contain object references instead of uploaded image Base64 after asset migration.
