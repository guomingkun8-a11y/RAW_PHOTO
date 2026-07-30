# RAW_PHOTO

RAW_PHOTO 是一个面向电商场景的 AI 生图工作台，主要用于主图、车图、白底图、套图换图、详情页素材等图片生产。项目支持多账号登录、管理员账号管理、任务队列、API Key 池、单用户并发限制、图片历史保存、对象存储和内网部署。

## 功能概览

- 电商生图：支持文生图、图生图、参考图生成、批量任务和多画布比例。
- 账号系统：支持用户注册、登录、普通用户和管理员角色。
- 管理后台：管理员可管理用户、查看监控、查看任务状态和系统运行情况。
- 任务队列：生成任务进入队列，由 worker 消费，避免多人同时使用时互相拖垮服务。
- 并发控制：支持全局并发、单用户并发、单用户排队上限和 API Key 池。
- API Key 池：可配置多个 OpenAI-compatible 中转站 Key，按池化方式分配生成任务。
- 图片资产：支持本地保存，也支持对象存储，例如七牛云、MinIO/S3 兼容存储、阿里云 OSS S3 兼容模式。
- 监控与压测：内置任务队列压测脚本、监控接口和运行状态页面。
- 内网部署：支持本地局域网运行，也支持 Docker Compose 部署到服务器。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、TypeScript、Tailwind CSS、lucide-vue |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 队列 | Redis、Celery 或轻量 Redis worker |
| 数据库 | MySQL / PostgreSQL / SQLite，生产建议 MySQL 或 PostgreSQL |
| 图片处理 | Pillow |
| 对象存储 | 本地、七牛云、WebDAV、MinIO/S3 兼容存储 |
| 部署 | Docker、Docker Compose |
| 测试 | unittest、k6、本地 mock 压测脚本 |

## 项目结构

```text
RAW_PHOTO/
├── backend/                  # FastAPI 接口、业务服务、测试和迁移脚本
│   ├── api/                  # 登录、用户、图片任务、监控等 API
│   ├── services/             # 核心业务逻辑
│   ├── scripts/              # 数据库迁移、压测、初始化脚本
│   └── test/                 # 后端单元测试
├── frontend/                 # Vue 前端项目
│   ├── src/components/       # 页面组件
│   ├── src/pages/            # 登录、图片工作台、图库、用户管理、监控页
│   └── vite.config.ts        # 开发代理和构建配置
├── docs/                     # 部署、队列、安全和监控文档
├── scripts/                  # Windows 内网部署辅助脚本
├── data/                     # 本地运行数据，已被 Git 忽略
├── main.py                   # 后端应用入口
├── worker.py                 # 生图任务 worker 入口
├── Dockerfile
├── docker-compose.enterprise.yml
├── .env.example              # 环境变量示例
└── config.example.json       # 配置示例
```

## 本地开发运行

### 1. 准备依赖

需要提前安装：

- Python 3.13
- Node.js 22 或兼容版本
- Redis
- Git

建议使用 `uv` 管理 Python 依赖：

```powershell
pip install uv
uv sync
```

安装前端依赖：

```powershell
cd frontend
npm install
cd ..
```

### 2. 配置环境变量

复制示例环境文件：

```powershell
Copy-Item .env.example .env.local
```

然后编辑 `.env.local`，至少配置：

```text
GMKRAW_AUTH_KEY=replace-with-a-long-random-value
GMKRAW_OPENAI_RELAY_ENABLED=true
GMKRAW_OPENAI_RELAY_BASE_URL=https://your-relay.example.com/v1
GMKRAW_OPENAI_RELAY_API_KEY=replace-with-relay-api-key
IMAGE_TASK_QUEUE_ENABLED=true
IMAGE_TASK_REDIS_URL=redis://127.0.0.1:6379/0
```

如果使用多个中转站 Key，可使用环境变量或 `config.json` 配置 API Key 池。真实 Key 只放在 `.env.local` 或服务器环境变量里，不要提交到 GitHub。

### 3. 启动 Redis

```powershell
redis-server
```

确认 Redis 正常：

```powershell
Get-NetTCPConnection -LocalPort 6379 -State Listen
```

### 4. 启动后端

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --log-level info
```

后端接口地址：

```text
http://127.0.0.1:8002
http://127.0.0.1:8002/docs
```

### 5. 启动 worker

新开一个 PowerShell：

```powershell
cd "D:\raw photo"
.\.venv\Scripts\python.exe worker.py
```

worker 负责真正消费生图任务。只启动后端和前端，任务会入队但不会被处理。

### 6. 启动前端

新开一个 PowerShell：

```powershell
cd "D:\raw photo\frontend"
npm run dev -- --host 0.0.0.0 --port 4399
```

本机访问：

```text
http://127.0.0.1:4399/image
```

局域网访问：

```text
http://你的电脑局域网IP:4399/image
```

例如：

```text
http://192.168.9.94:4399/image
```

## Docker 内网部署

生产或公司内网建议使用 Docker Compose，服务端统一由容器托管。

### 1. 准备服务器

服务器需要安装：

- Docker
- Docker Compose
- Git

如果使用 Docker 部署，服务器不需要手动安装 Python、Node、前端依赖和后端依赖，这些会在镜像构建时处理。

### 2. 创建部署环境文件

```powershell
Copy-Item .env.example .env.intranet
```

编辑 `.env.intranet`，至少填写：

```text
GMKRAW_AUTH_KEY
MYSQL_PASSWORD
MYSQL_ROOT_PASSWORD
REDIS_PASSWORD
GMKRAW_OPENAI_RELAY_BASE_URL
GMKRAW_OPENAI_RELAY_API_KEY
```

如果使用七牛云或阿里云 OSS，还需要配置对应对象存储参数。

### 3. 启动企业版内网栈

```powershell
.\scripts\start-intranet.ps1 -EnvFile .env.intranet -WorkerReplicas 1 -Build
```

访问：

```text
http://服务器局域网IP:8000
http://服务器局域网IP:8000/image
http://服务器局域网IP:8000/monitoring
```

增加 worker 副本：

```powershell
.\scripts\start-intranet.ps1 -EnvFile .env.intranet -WorkerReplicas 2
```

停止服务：

```powershell
.\scripts\stop-intranet.ps1 -EnvFile .env.intranet
```

## 关键配置说明

### 生图中转站

```text
GMKRAW_OPENAI_RELAY_ENABLED=true
GMKRAW_OPENAI_RELAY_BASE_URL=https://your-relay.example.com/v1
GMKRAW_OPENAI_RELAY_API_KEY=replace-with-relay-api-key
```

如果使用多个 Key，建议把 Key 池放到环境变量或未提交的 `config.json` 中。配置多个 Key 后，系统可以在并发任务中分配空闲 Key，减少多人使用时的排队时间。

### 队列和并发

```text
IMAGE_TASK_QUEUE_ENABLED=true
IMAGE_TASK_EXECUTOR=celery
IMAGE_TASK_REDIS_URL=redis://:password@redis:6379/0
IMAGE_TASK_TOTAL_CONCURRENCY=5
IMAGE_TASK_WORKER_CONCURRENCY=5
IMAGE_TASK_OWNER_CONCURRENCY=2
IMAGE_TASK_OWNER_PENDING_LIMIT=30
IMAGE_TASK_MAX_RETRIES=2
```

含义：

- `IMAGE_TASK_TOTAL_CONCURRENCY`：全局同时生成任务数量上限。
- `IMAGE_TASK_WORKER_CONCURRENCY`：单个 worker 的并发能力。
- `IMAGE_TASK_OWNER_CONCURRENCY`：单个用户同时生成任务数量上限。
- `IMAGE_TASK_OWNER_PENDING_LIMIT`：单个用户排队加运行的任务上限。
- `IMAGE_TASK_MAX_RETRIES`：失败自动重试次数。

建议先保守设置，例如：

```text
IMAGE_TASK_TOTAL_CONCURRENCY=5
IMAGE_TASK_WORKER_CONCURRENCY=5
IMAGE_TASK_OWNER_CONCURRENCY=2
IMAGE_TASK_OWNER_PENDING_LIMIT=30
```

稳定后再根据监控和压测结果逐步增加全局并发。

### 对象存储

本地开发可以先使用本地存储。长期多人使用建议开启对象存储，避免生成图片只保存在某一台电脑上。

常见模式：

```text
GMKRAW_IMAGE_STORAGE_ENABLED=true
GMKRAW_IMAGE_STORAGE_MODE=both
GMKRAW_IMAGE_STORAGE_PROVIDER=minio
GMKRAW_IMAGE_STORAGE_PUBLIC_BASE_URL=https://your-bucket-public-domain/path
GMKRAW_MINIO_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
GMKRAW_MINIO_ACCESS_KEY=replace-with-access-key
GMKRAW_MINIO_SECRET_KEY=replace-with-secret-key
GMKRAW_MINIO_BUCKET=replace-with-bucket
GMKRAW_MINIO_REGION=cn-beijing
GMKRAW_MINIO_ROOT_PATH=raw-photo/images
GMKRAW_MINIO_SECURE=true
```

阿里云 OSS 可通过 S3 兼容方式接入，项目里使用 `minio` provider 对接即可。

## 用户和权限

项目当前设计为两类角色：

- 普通用户：登录后使用图片生成、查看自己的任务和图片历史。
- 管理员：管理用户、查看监控、处理账号启停和权限调整。

当前不设计复杂的超级管理员体系，适合公司内部工具场景。管理员账号应只分配给少数维护人员。

## 测试

### 后端单元测试

```powershell
$env:PYTHONPATH = "D:\raw photo\backend"
.\.venv\Scripts\python.exe -m unittest discover backend.test
```

也可以只跑重点测试：

```powershell
$env:PYTHONPATH = "D:\raw photo\backend"
.\.venv\Scripts\python.exe -m unittest backend.test.test_openai_relay_service backend.test.test_image_task_service
```

### 前端构建检查

```powershell
cd frontend
npm run build
```

### 队列压测

本地 mock 压测不会调用真实上游生图 API，适合用来验证队列、并发和数据库写入能力：

```powershell
uv run python backend/scripts/run_image_task_load_test.py `
  --users 60 `
  --api-instances 4 `
  --workers 5 `
  --total-concurrency 5 `
  --owner-concurrency 2 `
  --owner-burst-tasks 12 `
  --handler-delay-ms 500
```

重点观察：

- `queue.max_depth`：峰值队列深度。
- `queue.peak_slot_utilization`：全局并发 slot 是否打满。
- `workers.worker_utilization_avg`：worker 平均利用率。
- `single_owner.max_queued_tasks`：单用户排队压力。
- `results.failure_rate`：失败率。
- `recommendations`：脚本给出的调参建议。

### k6 压测

项目包含 k6 脚本：

```text
scripts/k6-frontend-static.js
scripts/k6-image-workspace.js
```

安装 k6 后可以根据脚本里的目标地址压测前端页面和图片工作台接口。真实生图压测会消耗上游额度，建议先用 mock 脚本验证本地系统承载能力。

## 常见问题

### 为什么别人访问不了我的电脑？

如果项目只运行在你的电脑上，你电脑关机、断网或服务关闭，其他人就无法访问。公司多人长期使用建议部署到服务器，并使用 Docker Compose 托管服务。

### 为什么多人同时生成会排队？

排队通常来自三个限制：

- 全局并发 `IMAGE_TASK_TOTAL_CONCURRENCY`
- 单用户并发 `IMAGE_TASK_OWNER_CONCURRENCY`
- 上游 API Key 或中转站限流

增加 API Key 池可以提升可用并发，但不能无限扩大。全局并发应根据失败率、响应时间、上游限流和服务器资源逐步调。

### 为什么要保留 worker？

前端点击生成后，后端只负责创建任务和入队。真正调用生图接口的是 worker。worker 没有运行时，任务会一直排队。

### 生成图片要不要永久保存？

本地 `data/` 可以保存历史，但不适合多人长期依赖。建议开启对象存储，并将图片 URL 保存到数据库。这样服务器迁移或重启后，用户历史图片仍然可访问。

## 安全注意事项

- 不要提交 `.env.local`、`.env`、`config.json`、`data/`。
- 不要把 API Key、阿里云 AccessKey、七牛云密钥、数据库密码写进 README。
- GitHub 公开仓库中只保留 `.env.example` 和 `config.example.json` 这种占位示例。
- 已经泄露过的 Key 建议轮换。
- 生产环境建议使用 RAM 子账号，并按最小权限授权对象存储。
- 管理员账号建议开启强密码，后续可接入验证码、审计日志和访问白名单。

## Git 工作流建议

日常开发建议：

```powershell
git status
git add .
git commit -m "描述本次修改"
git push
```

如果部署在服务器上，建议区分：

- `main`：稳定可部署代码。
- `dev`：测试环境开发分支。

小功能可以先在测试环境验证，通过后再合并到 `main` 并部署生产环境。

## 许可证

本项目使用 MIT License。详见 [LICENSE](LICENSE)。
