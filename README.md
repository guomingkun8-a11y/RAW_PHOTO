# RAW_PHOTO

RAW_PHOTO 是一个面向电商团队的多模态 AI 创作与生产工作台，支持商品图片、营销视频、配音音频和无限画布工作流。团队成员可以在同一套系统中提交创作任务、管理素材和提示词模板、查看生成进度与费用，并向管理员反馈使用问题。

项目采用 Vue 3 + FastAPI，使用 MySQL 保存业务数据、Redis 调度异步任务，可接入 Qdrant 向量检索和 OSS / S3 对象存储，支持本地局域网运行与服务器部署。

[功能概览](#功能概览) · [最新功能](#最新功能2026-10-25) · [模型目录](#当前内置模型) · [本地运行](#本地开发运行) · [Docker 部署](#docker-内网部署) · [更新记录](CHANGELOG.md)

## 功能概览

- 图片生成：支持文生图、图生图、多图参考、文件夹批量生图、批量换商品和多画布比例。
- 专业图片 Agent：支持多轮创作、商品与人物一致性、视觉分析、营销策略、知识检索、长期记忆和批量方案执行。
- 视频生成：支持文生视频、图生视频、视频参考，以及部分模型的图像、视频、音频联合参考、视频编辑和延长。
- 视频助手与时间线：支持视频理解、创作方案、素材编排、配音、语音转写、字幕及异步合成任务。
- 音频生成：支持 `doubao-tts-2.0` 和 `gem-3.1-tts`，覆盖多音色、语速、情感、单人朗读和双人对话。
- 无限画布：支持图片、视频、音频和文本节点编排、工作流保存、创意助手、分镜脚本以及生成任务状态跟踪。
- 提示词模板：支持模板创建、管理和一键应用，沉淀团队常用创作方案。
- 统一历史：集中查看和管理图片、视频、音频及生成任务记录。
- 账号系统：支持用户注册、登录、普通用户和管理员角色。
- 管理后台：支持用户管理、任务与费用查询、公告管理，以及全员导航排序和显示设置。
- 实时通知：使用 SSE 推送系统公告和图片任务状态变化，页面无需持续高频刷新。
- 用户反馈：支持问题、建议、使用咨询、截图附件、管理员回复、未读提醒和处理状态跟踪。
- 运维告警：监测后端、数据库、Redis、Worker 与队列状态，支持企业微信通知、告警去重和恢复提醒。
- 任务队列：图片、视频、音频、Agent 和视频合成任务由独立 Worker 异步消费。
- 并发控制：支持全局并发、单用户并发、单用户排队上限和 API Key 池。
- API Key 池：可配置多个 OpenAI-compatible 中转站 Key，按池化方式分配生成任务。
- 素材存储：支持本地、WebDAV、MinIO/S3 兼容存储和阿里云 OSS，并可为结果生成受控访问地址。
- 监控与计费：提供任务明细、成功/失败统计、队列状态、用户/模型费用和上游用量对账。
- 内网部署：支持本地局域网运行，也支持 Docker Compose 部署到服务器。

## 最新功能（2026-10-25）

### 用户反馈与问题跟进

用户可以从顶部“问题反馈”入口提交功能异常、需求建议或使用咨询，附上文字和截图，并在反馈列表中继续查看管理员回复。每条消息最多支持 6 张 PNG、JPG 或 WebP 截图，每张不超过 8 MB。

管理员可在反馈管理页面搜索、筛选和认领问题，回复用户、添加仅管理员可见的内部备注，并调整“待处理、处理中、待补充、已解决”状态。用户可以重新打开已解决的问题。

消息通过 HTTP 接口提交并持久化，WebSocket 用于实时同步，Redis Pub/Sub 用于跨进程转发，另有定时同步作为补充。配置企业微信机器人后，还可接收用户消息与管理员公开回复的通知。

### 公告、导航与运行告警

- 公告管理：在系统管理中创建、编辑、保存未发布公告、发布或下架，支持普通通知、功能更新、重要提醒和故障通知。
- 未读提醒：已发布公告通过 SSE 同步；新增或更新内容显示红色未读角标，阅读状态按用户和公告版本在当前浏览器保存。
- 导航布局：初始管理员可调整全员侧栏顺序与显示状态，管理员专属菜单仍受权限限制。
- 运行告警：检测服务不可用、数据库或 Redis 连接异常、Worker 离线、队列积压、死信和长时间未更新任务。
- 通知管理：支持多个企业微信机器人，分别选择接收运行告警或反馈消息；运行告警支持冷却、重复提醒、恢复通知和历史记录。

### 任务恢复与费用核对

图片任务支持上游结果继续核对和迟到结果恢复，减少本地等待结束后丢失上游结果的情况；视频任务支持状态查询、结果核对和历史管理。费用模块记录上游用量及用户、模型、任务来源归属，便于核对本地生成记录与上游账单。

已提交上游的图片任务在取消前会提示二次确认。本地取消无法保证上游终止生成，任务仍可能完成并产生费用；任务状态和计费结果需要分别核对。

## 当前内置模型

| 类型 | 模型 |
| --- | --- |
| 图片 | `banana-2`、`gpt-image-2`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` |
| 文生视频 | `hailuo-h3`、`doubao-seedance-2-5-260628`、`kling-v3-video` |
| 图生视频 | `hailuo-h3-cankaosheng`、`hailuo-h3-max-shouweizhen`、`gk-video-3.5`、`doubao-seedance-2-5-cankaosheng` |
| 全能参考与视频参考 | `seedance-2.0-guanfang`、`doubao-seedance-2-5-quannengcankao`、`wan3.0-video-quannengcankao`、`hailuo-h3-quannengcankao`、`wan2.7-cankaosheng` |
| 音频 | `doubao-tts-2.0`、`gem-3.1-tts` |

图片工作台当前展示上述 4 个图片模型。视频模型的参考素材类型、数量、时长、清晰度和编辑能力由模型目录分别约束；全能参考模型并非都支持相同的功能。实际可用模型、参数和价格以页面及所配置的上游渠道为准。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、TypeScript、Tailwind CSS、lucide-vue |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 任务调度 | Redis、Celery 或轻量 Redis Worker、独立媒体与 Agent Worker |
| 实时通信 | SSE 公告与任务事件、WebSocket 反馈同步、Redis Pub/Sub |
| 数据库 | MySQL / PostgreSQL / SQLite，生产建议 MySQL 或 PostgreSQL |
| 向量检索 | Qdrant、OpenAI-compatible Embedding |
| 媒体处理 | Pillow、FFmpeg / FFprobe |
| 对象存储 | 本地、WebDAV、MinIO/S3 兼容存储、阿里云 OSS |
| 运行告警 | 独立监控进程、告警记录、企业微信机器人 Webhook |
| 部署 | Docker、Docker Compose |
| 测试 | unittest、Playwright、k6、本地 mock 压测脚本 |

## 项目结构

```text
RAW_PHOTO/
├── backend/                  # FastAPI 接口、业务服务、测试和迁移脚本
│   ├── api/                  # 创作、反馈、告警、导航和系统 API
│   ├── services/             # 创作、计费、存储、反馈、告警和平台服务
│   ├── monitor_worker.py     # 独立运行监控与告警进程
│   ├── scripts/              # 数据库迁移、压测、初始化脚本
│   └── test/                 # 后端单元测试
├── frontend/                 # Vue 前端项目
│   ├── src/components/       # 通用组件和图片工作台组件
│   ├── src/features/         # 画布、视频时间线、计费、反馈、告警和公告管理
│   ├── src/pages/            # 图片、视频、音频、画布、历史和管理页面
│   └── vite.config.ts        # 开发代理和构建配置
├── docs/                     # 部署、队列、安全和监控文档
├── scripts/                  # Windows 内网部署辅助脚本
├── data/                     # 本地运行数据，已被 Git 忽略
├── logs/                     # 运行日志和本地备份，已被 Git 忽略
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
- MySQL（推荐用于团队业务数据）
- Redis
- Git

先获取代码，再从项目根目录完成后续配置：

```powershell
git clone https://github.com/guomingkun8-a11y/RAW_PHOTO.git
cd RAW_PHOTO
```

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
MYSQL_DATABASE_URL=mysql+pymysql://raw_photo:replace-with-db-password@127.0.0.1:3306/raw_photo?charset=utf8mb4
```

先在 MySQL 中创建相应数据库和用户，再填写数据库连接配置。数据库密码包含特殊字符时，需要进行 URL 编码。

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

需要持续运行健康检查与企业微信告警时，再启动独立监控进程：

```powershell
.\.venv\Scripts\python.exe backend\monitor_worker.py
```

监控进程需要与后端使用一致的数据库、Redis 和告警配置，详细设置见下文“反馈与告警配置”。

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

反向代理部署时，需要同时转发 `/api/feedback/ws` 的 WebSocket 升级请求，并允许 `/api/events/stream` 持续返回 SSE 数据。Vite 开发代理已配置 WebSocket 转发。

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

专业 Agent 的 run、事件和取消状态保存在 MySQL，Redis Streams 负责持久排队、分布式并发槽、用户会话锁与 SSE 事件唤醒。部署时必须同时运行 `agent_worker.py`；多个 app 实例和 Agent worker 需要共用 MySQL、Redis 以及 `data` 目录。

一次生成多张图片时，系统会将生成请求拆分成独立任务，通过队列和多个 Worker 按并发限制执行。普通上传和参考图分析最多支持 20 张图片；单次生图最多使用 14 张参考图。这些限制分别约束上传数量和单个生成任务的参考图数量。

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

### 反馈与告警配置

反馈实时同步默认使用图片队列的 Redis，也可通过 `FEEDBACK_REDIS_URL` 单独指定。反馈会话、消息、截图元数据、已读位置和通知待投递事件保存在业务数据库中。截图上传需要配置参考图上传所使用的 OSS / S3 对象存储，截图通过鉴权接口读取。

运行告警的环境变量示例：

```text
WECOM_ALERT_ENABLED=true
WECOM_ALERT_ENVIRONMENT=production
WECOM_ALERT_INTERNAL_TOKEN=replace-with-a-long-random-monitor-token
WECOM_ALERT_CHECK_INTERVAL_SECS=30
GMKRAW_BASE_URL=http://127.0.0.1:8002
```

`GMKRAW_BASE_URL` 应指向监控进程能访问的实际后端地址；`WECOM_ALERT_INTERNAL_TOKEN` 在后端和监控进程中需要一致。以上仅为本机端口示例，部署时应按实际地址填写。

初始管理员可以在“系统管理 → 监控告警”中配置企业微信机器人、告警开关和阈值，并选择机器人接收告警或用户反馈。需要反馈通知时，再到反馈管理的“通知设置”启用推送，并填写用户可以访问的系统地址。

## 用户和权限

项目使用普通用户和管理员两类角色，部分系统级操作进一步限制为初始管理员：

- 普通用户：使用已开放的创作功能、管理自己的历史资产、提交反馈并查看回复。
- 管理员：管理用户、查看监控与费用、处理用户反馈及内部备注。
- 初始管理员：额外通过系统管理页面管理全员导航、公告、运行告警与机器人配置，以及反馈通知设置。

导航显示状态用于组织页面入口，接口仍独立校验身份和权限。管理员账号应只分配给负责维护和问题处理的人员。

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

### 为什么取消或失败的任务仍可能计费？

如果请求已被上游接受，本地取消、等待超时或网络中断并不代表上游已经停止执行。系统会对能够查询的上游任务继续核对结果和费用；具体扣费与退款应以上游最终账单和系统同步记录为准。

### 为什么反馈能提交，但没有实时收到回复？

反馈提交使用 HTTP，实时同步使用 WebSocket。请检查后端 WebSocket 依赖、代理升级转发及 Redis 连接；连接恢复前，页面仍可通过定时同步获取消息。企业微信反馈提醒还需要单独配置机器人并启用通知。

### 生成图片要不要永久保存？

本地 `data/` 可以保存历史，但不适合多人长期依赖。建议开启对象存储，并将图片 URL 保存到数据库。这样服务器迁移或重启后，用户历史图片仍然可访问。

## 安全注意事项

- 不要提交 `.env.local`、`.env`、`config.json`、`data/`、`logs/`、`tmp/`。
- 不要把 API Key、阿里云 AccessKey、数据库密码写进 README。
- GitHub 公开仓库中只保留 `.env.example` 和 `config.example.json` 这种占位示例。
- 已经泄露过的 Key 建议轮换。
- 生产环境建议使用 RAM 子账号，并按最小权限授权对象存储。
- 使用强管理员密码，按实际部署需要配置验证码、访问控制与审计策略。

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
