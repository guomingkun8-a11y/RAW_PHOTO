# 内网部署与容量验证

这套部署面向十几人到几十人的内网试用环境，固定为：

- 一个内网入口：`app` 容器对外暴露 `8000`，后端同时托管前端 `frontend/dist`。
- 后端任务队列：`worker` 消费 Redis 队列。
- 状态存储：MySQL 保存账号、图库、任务、监控事件。
- 队列传输：Redis 只负责任务投递、全局并发 slot 和 worker 心跳。
- 图片资产：参考图和长期图片资产使用阿里云 OSS；生成图存储通过 `minio` provider 接入 OSS S3 兼容接口。

## 推荐起步参数

十几人内网试用先用保守参数，稳定后再加：

```text
IMAGE_TASK_EXECUTOR=celery
IMAGE_TASK_TOTAL_CONCURRENCY=5
IMAGE_TASK_WORKER_CONCURRENCY=5
IMAGE_TASK_OWNER_CONCURRENCY=2
IMAGE_TASK_OWNER_PENDING_LIMIT=30
IMAGE_TASK_MAX_RETRIES=2
IMAGE_TASK_DB_POOL_SIZE=10
IMAGE_TASK_DB_MAX_OVERFLOW=20
```

含义：

- `total_concurrency`：全局同时生图上限，优先保护账号池和上游。
- `worker_concurrency`：单个 worker 容器里的 Celery 并发数。
- `owner_concurrency`：单个用户同时 running 的任务数。
- `owner_pending_limit`：单个用户 queued + running 的最大任务数。

如果账号池少、429 多、失败率上升，先降 `total_concurrency`。如果队列变长但失败率低，再加 worker 或提高并发。

## 启动流程

1. 从模板创建内网环境文件：

```powershell
Copy-Item .env.example .env.intranet
```

2. 编辑 `.env.intranet`，至少填好：

```text
GMKRAW_AUTH_KEY
MYSQL_PASSWORD
MYSQL_ROOT_PASSWORD
REDIS_PASSWORD
GMKRAW_OSS_ENDPOINT
GMKRAW_OSS_ACCESS_KEY_ID
GMKRAW_OSS_ACCESS_KEY_SECRET
GMKRAW_OSS_BUCKET
GMKRAW_OSS_REGION
GMKRAW_OPENAI_RELAY_BASE_URL
GMKRAW_OPENAI_RELAY_API_KEY
```

3. 启动完整内网栈：

```powershell
.\scripts\start-intranet.ps1 -EnvFile .env.intranet -WorkerReplicas 2 -Build
```

4. 浏览器访问：

```text
http://服务器内网IP:8000
http://服务器内网IP:8000/monitoring
```

5. 如果需要扩 worker 副本：

```powershell
.\scripts\start-intranet.ps1 -EnvFile .env.intranet -WorkerReplicas 2
```

注意：worker 副本数变多不代表总并发一定变多，最终仍受 `IMAGE_TASK_TOTAL_CONCURRENCY` 的 Redis 全局 slot 限制。

6. 停止：

```powershell
.\scripts\stop-intranet.ps1 -EnvFile .env.intranet
```

## 小规模压测

本地不打真实上游，只压 API 入库、Redis 队列、worker 消费、全局 slot、单用户排队：

```powershell
uv run python backend/scripts/run_image_task_load_test.py `
  --users 30 `
  --api-instances 3 `
  --workers 5 `
  --total-concurrency 5 `
  --owner-concurrency 2 `
  --owner-burst-tasks 8 `
  --handler-delay-ms 250
```

如果在 Docker 内测企业栈，用容器内地址：

```bash
docker compose --env-file .env.intranet -f docker-compose.enterprise.yml exec app \
  uv run python backend/scripts/run_image_task_load_test.py \
  --users 60 \
  --api-instances 4 \
  --workers 5 \
  --total-concurrency 5 \
  --owner-concurrency 2 \
  --owner-burst-tasks 12 \
  --handler-delay-ms 500 \
  --redis-url redis://:${REDIS_PASSWORD}@redis:6379/0 \
  --database-url mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@mysql:3306/${MYSQL_DATABASE}?charset=utf8mb4
```

重点看输出里的：

- `queue.max_depth`：峰值队列深度。
- `queue.peak_slot_utilization`：全局 slot 是否被打满。
- `workers.worker_utilization_avg`：worker 平均忙碌程度。
- `single_owner.max_queued_tasks`：单用户是否被合理排队。
- `results.failure_rate`：失败率。
- `recommendations`：脚本按结果给出的调参建议。

## 调参规则

- 本机 MySQL + Redis mock 压测中，`total_concurrency=5`、`worker_concurrency=5`、`owner_concurrency=2` 的 60 用户任务吞吐约为 9.91 task/s，失败率 0，单用户 running 稳定限制为 2。
- 队列持续增长，`peak_slot_utilization >= 0.85`，失败率低：可以把 `total_concurrency` 从 5 调到 6，同时把 `worker_concurrency` 对齐。
- 队列持续增长，但 `avg_slot_utilization < 0.5`：优先查 worker 是否在线、Celery 是否消费、Redis 是否正常，不急着加总并发。
- 失败率超过 5%，尤其出现 429、限流、账号不可用：先降 `total_concurrency`，再补账号池或中转资源。
- 单用户排队明显但整体队列健康：保持 `owner_concurrency=2`，这是公平策略；只给可信内部用户场景临时提高。
- worker 数大于 `total_concurrency` 时，多出来的 worker 只是备用容量，不会突破全局限制。

## 告警阈值

建议每 1 分钟看一次 `/monitoring` 或 `/api/monitoring/summary`，连续多次触发再处理。

| 指标 | 预警 | 严重 |
| --- | --- | --- |
| `active_workers` | 低于期望 worker 数 3 分钟 | 等于 0 超过 1 分钟 |
| `queue_depth` | 大于 `total_concurrency * 5` 持续 5 分钟 | 大于 `total_concurrency * 15` 持续 5 分钟 |
| `active_slots / total_concurrency` | 超过 90% 且队列增长 5 分钟 | 长时间 100% 且失败率上升 |
| `stale_running_tasks` | 大于 0 | 大于 0 持续 2 轮检查 |
| `failure_rate` | 最近 10 分钟超过 5% | 最近 10 分钟超过 15% |
| `task_latency.p95_ms` | 超过历史基线 2 倍 | 超过 180 秒且队列增长 |
| 单用户 `active_tasks` | 达到 `owner_pending_limit * 0.8` | 达到 `owner_pending_limit` |

## 日常排查顺序

1. 看 `/monitoring`：worker 是否在线，队列是否堆积。
2. 看失败率：如果失败率上升，先保护账号池，不要盲目加并发。
3. 看 `active_slots`：打满说明全局并发是瓶颈，没打满说明 worker 或队列消费有问题。
4. 看单用户负载：单个用户排队不等于系统崩，是并发公平限制在生效。
5. 看日志：`docker compose --env-file .env.intranet -f docker-compose.enterprise.yml logs -f app worker`。
