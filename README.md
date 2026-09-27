# RAW_PHOTO

RAW_PHOTO 是一个面向电商团队的多模态 AI 创作与生产工作台，覆盖图片、视频、音频和无限画布工作流。项目将模型调用、异步队列、素材历史、提示词模板、实时进度、费用监控和团队权限集中在同一套内网系统中。

## 功能概览

- 图片生成：支持文生图、图生图、多图参考、文件夹批量生图、批量换商品和多画布比例。
- 专业图片 Agent：支持多轮创作、商品与人物一致性、视觉分析、营销策略、知识检索、长期记忆和批量方案执行。
- 视频生成：支持文生视频、图生视频、多图参考、首尾帧、任务取消、失败重试、结果持久化和历史管理。
- 视频助手与时间线：支持视频理解、创作方案、素材编排、配音、语音转写、字幕及异步合成任务。
- 音频生成：支持 `doubao-tts-2.0` 和 `gem-3.1-tts`，覆盖多音色、语速、情感、单人朗读和双人对话。
- 无限画布：支持图片、视频和文本节点编排、工作流保存、创意助手、分镜脚本以及生成任务状态跟踪。
- 提示词模板：支持模板创建、管理和一键应用，沉淀团队常用创作方案。
- 统一历史：集中查看和管理图片、视频、音频及生成任务记录。
- 账号系统：支持用户注册、登录、普通用户和管理员角色。
- 管理后台：管理员可管理用户、查看任务状态、队列、模型费用和系统运行情况。
- 实时通知：使用 SSE 推送系统公告和图片任务状态变化，页面无需持续高频刷新。
- 任务队列：图片、视频、音频、Agent 和视频合成任务由独立 Worker 异步消费。
- 并发控制：支持全局并发、单用户并发、单用户排队上限和 API Key 池。
- API Key 池：可配置多个 OpenAI-compatible 中转站 Key，按池化方式分配生成任务。
- 素材存储：支持本地、WebDAV、MinIO/S3 兼容存储和阿里云 OSS，并可为结果生成受控访问地址。
- 监控与计费：提供任务明细、成功/失败统计、队列状态、用户/模型费用和上游用量对账。
- 内网部署：支持本地局域网运行，也支持 Docker Compose 部署到服务器。

## 当前内置模型

| 类型 | 模型 |
| --- | --- |
| 图片 | `banana-2`、`gpt-image-2`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` |
| 文生视频 | `hailuo-h3`、`doubao-seedance-2-5-260628`、`kling-v3-video` |
| 图生视频 | `hailuo-h3-cankaosheng`、`hailuo-h3-max-shouweizhen`、`gk-video-3.5`、`doubao-seedance-2-5-cankaosheng` |
| 音频 | `doubao-tts-2.0`、`gem-3.1-tts` |

模型是否可用仍取决于实际配置的上游渠道、API Key 和账户权限。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、TypeScript、Tailwind CSS、lucide-vue |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 队列与实时事件 | Redis、Celery 或轻量 Redis Worker、SSE |
| 数据库 | MySQL / PostgreSQL / SQLite，生产建议 MySQL 或 PostgreSQL |
| 向量检索 | Qdrant、OpenAI-compatible Embedding |
| 媒体处理 | Pillow、FFmpeg / FFprobe |
| 对象存储 | 本地、WebDAV、MinIO/S3 兼容存储、阿里云 OSS |
| 部署 | Docker、Docker Compose |
| 测试 | unittest、Playwright、k6、本地 mock 压测脚本 |

## 项目结构

```text
RAW_PHOTO/
├── backend/                  # FastAPI 接口、业务服务、测试和迁移脚本
│   ├── api/                  # 图片、视频、音频、画布、计费和系统 API
│   ├── services/             # 图片、视频、音频、Agent、存储和平台服务
│   ├── scripts/              # 数据库迁移、压测、初始化脚本
│   └── test/                 # 后端单元测试
├── frontend/                 # Vue 前端项目
│   ├── src/components/       # 通用组件和图片工作台组件
│   ├── src/features/         # 音频、无限画布、视频时间线和计费模块
│   ├── src/pages/            # 图片、视频、音频、画布、历史和管理页面
│   └── vite.config.ts        # 开发代理和构建配置
├── docs/                     # 部署、队列、安全和监控文档
├── scripts/                  # Windows 内网部署辅助脚本
├── data/                     # 本地运行数据，已被 Git 忽略
├── main.py                   # 后端应用入口
├── worker.py                 # 图片生成 Worker
├── agent_worker.py           # 专业图片 Agent Worker
├── video_worker.py           # 视频分析 Worker
├── video_generation_worker.py # 视频生成 Worker
├── audio_generation_worker.py # 音频生成 Worker
├── video_composition_worker.py # 视频合成 Worker
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
GMKRAW_LEGACY_AUTH_KEY_ADMIN_ENABLED=false
GMKRAW_OPENAI_RELAY_ENABLED=true
GMKRAW_OPENAI_RELAY_BASE_URL=https://your-relay.example.com/v1
GMKRAW_OPENAI_RELAY_API_KEY=replace-with-relay-api-key
IMAGE_TASK_QUEUE_ENABLED=true
IMAGE_TASK_REDIS_URL=redis://127.0.0.1:6379/0
```

如果使用多个中转站 Key，可使用环境变量或 `config.json` 配置 API Key 池。真实 Key 只放在 `.env.local` 或服务器环境变量里，不要提交到 GitHub。

`GMKRAW_LEGACY_AUTH_KEY_ADMIN_ENABLED` 默认建议保持 `false`。只有需要临时兼容旧版 `auth-key` 管理员访问方式时才打开，正式环境应使用账号登录和管理员权限管理。

### 3. 启动 Redis

```powershell
redis-server
```

确认 Redis 正常：

```powershell
Get-NetTCPConnection -LocalPort 6379 -State Listen
```

### 4. 启动 Qdrant

专业 Agent 的长期记忆和文件夹视觉分析可以使用 Qdrant。Docker Desktop 启动后执行：

```powershell
docker compose -f docker-compose.local.yml up -d qdrant
```

本地 Dashboard：

```text
http://127.0.0.1:6333/dashboard
```

本地 `.env.local` 配置：

```text
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=raw_professional_memory
QDRANT_FOLDER_COLLECTION=raw_professional_folder_assets
QDRANT_KNOWLEDGE_COLLECTION=raw_professional_knowledge
GMKRAW_EMBEDDING_BASE_URL=https://embedding.example.com/v1
GMKRAW_EMBEDDING_API_KEY=replace-with-embedding-api-key
GMKRAW_EMBEDDING_MODEL=text-embedding-3-small
```

Qdrant 只负责保存和检索向量，Embedding 由 `GMKRAW_EMBEDDING_BASE_URL` 指向的 OpenAI 兼容 `/v1/embeddings` 服务生成。本地模式在 Embedding 暂时不可用时会退回关键词检索；企业模式要求知识库和长期记忆向量回填完整，否则 `schema-init` 会拒绝启动 app 和 worker。

配置好 Embedding 后可手动执行一次全量回填：

```powershell
.\.venv\Scripts\python.exe backend\scripts\reindex_professional_vectors.py
```

### 5. 启动后端

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --log-level info
```

后端接口地址：

```text
http://127.0.0.1:8002
http://127.0.0.1:8002/docs
```

### 6. 启动任务 Worker

新开一个 PowerShell：

```powershell
cd "D:\raw photo"
.\.venv\Scripts\python.exe worker.py
```

`worker.py` 负责真正消费图片生成任务。只启动后端和前端，图片任务会入队但不会被处理。

根据启用的功能，分别启动对应 Worker：

```powershell
# 专业图片 Agent
.\.venv\Scripts\python.exe agent_worker.py

# 视频分析
.\.venv\Scripts\python.exe video_worker.py

# 视频生成
.\.venv\Scripts\python.exe video_generation_worker.py

# 音频生成
.\.venv\Scripts\python.exe audio_generation_worker.py

# 视频配音、字幕和时间线合成
.\.venv\Scripts\python.exe video_composition_worker.py
```

未在配置中启用的队列不需要启动相应 Worker。专业 Agent Worker 负责对话、文件夹分析、批计划执行和长期记忆蒸馏。

### 7. 启动前端

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
GMKRAW_EMBEDDING_BASE_URL
GMKRAW_EMBEDDING_API_KEY
GMKRAW_EMBEDDING_MODEL
GMKRAW_OSS_ACCESS_KEY_ID
GMKRAW_OSS_ACCESS_KEY_SECRET
GMKRAW_OSS_BUCKET
```

企业模式固定使用 MySQL、Redis 队列、Qdrant 和远端对象存储，并拒绝任何 `sqlite://` 业务数据库配置。`schema-init` 会先执行数据库迁移、同步专业知识正文，再回填知识库和长期记忆向量；任一步未完成都不会启动正式服务。

### 3. 启动企业版内网栈

```powershell
.\scripts\start-intranet.ps1 -EnvFile .env.intranet -WorkerReplicas 2 -Build
```

访问：

```text
http://服务器局域网IP:8000
http://服务器局域网IP:8000/image
http://服务器局域网IP:8000/video-generation
http://服务器局域网IP:8000/audio-generation
http://服务器局域网IP:8000/infinite-canvas
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
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_ENABLED=false
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_THRESHOLD=10
IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_MAX=20
IMAGE_TASK_OWNER_PENDING_LIMIT=30
IMAGE_TASK_MAX_RETRIES=2
AGENT_QUEUE_ENABLED=true
AGENT_REDIS_URL=redis://:password@redis:6379/0
AGENT_WORKER_CONCURRENCY=4
AGENT_TOTAL_CONCURRENCY=4
AGENT_OWNER_CONCURRENCY=1
AGENT_OWNER_PENDING_LIMIT=10
```

含义：

- `IMAGE_TASK_TOTAL_CONCURRENCY`：全局同时生成任务数量上限。
- `IMAGE_TASK_WORKER_CONCURRENCY`：单个 worker 的并发能力。
- `IMAGE_TASK_OWNER_CONCURRENCY`：单个用户同时生成任务数量上限。
- `IMAGE_TASK_DYNAMIC_OWNER_CONCURRENCY_*`：低活跃用户时按全局空闲槽弹性提高单用户上限，超过阈值后回到固定上限。
- `IMAGE_TASK_OWNER_PENDING_LIMIT`：单个用户排队加运行的任务上限。
- `IMAGE_TASK_MAX_RETRIES`：失败自动重试次数。
- `AGENT_TOTAL_CONCURRENCY`：所有 Agent worker 的全局执行上限。
- `AGENT_WORKER_CONCURRENCY`：单个 Agent worker 的消费线程数。
- `AGENT_OWNER_CONCURRENCY`：单个用户同时执行的 Agent 数量上限。
- `AGENT_OWNER_PENDING_LIMIT`：单个用户排队加运行的 Agent 数量上限。

专业 Agent 的 run、事件和取消状态保存在 MySQL，Redis Streams 负责持久排队、分布式并发槽、用户会话锁与 SSE 事件唤醒。部署时必须同时运行 `agent-worker.py`；多个 app 实例和 Agent worker 需要共用 MySQL、Redis 以及 `data` 目录。

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

- 普通用户：登录后使用图片、视频、音频、无限画布、模板中心和自己的历史资产。
- 管理员：管理用户、查看监控与费用、处理账号启停和权限调整。

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

### 前端浏览器自动化测试

Playwright 测试会在真实 Chromium 浏览器里跑登录、注册、图片生成和历史图库基础流程，测试请求使用 mock 数据，不会调用真实生图 API：

```powershell
cd frontend
npm run test:e2e
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

前端点击生成后，后端只负责创建任务和入队。真正调用模型或执行媒体处理的是对应 Worker；图片、视频、音频和视频合成都有各自的 Worker，未运行时相应任务会一直排队。

### 生成图片要不要永久保存？

本地 `data/` 可以保存历史，但不适合多人长期依赖。建议开启对象存储，并将图片 URL 保存到数据库。这样服务器迁移或重启后，用户历史图片仍然可访问。

## 安全注意事项

- 不要提交 `.env.local`、`.env`、`config.json`、`data/`。
- 不要把 API Key、阿里云 AccessKey、数据库密码写进 README。
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
