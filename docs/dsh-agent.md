# DSH 智能体

## 运行方式

本机生产使用 `.env.local` 中的 `DSH_RUNTIME_MODE=node` 和 Node.js 22.19 以上版本。Windows 平台 SDK 可执行包在此机器存在原生模块加载错误，因此单独部署了测试通过的 Node Runtime 构建产物，位置为本项目 `.venv/Lib/site-packages/deepseek_harness_runtime/runtime/node/`；升级或重建虚拟环境时需要重新部署并验证该产物，不能仅安装 PyPI 包。协作模式使用项目内锁定版本的 Node 宿主。运行时不依赖测试项目或 D 盘源码目录。生产默认模型为 `gem-3.5-flash`，全局并发 10、单用户并发 1、单用户等待上限 4、Runtime 池 10。Redis 复用生产实例的独立 `dsh_agent_jobs` 命名空间；会话数据保存在生产 `data/dsh-agent/` 下。

Windows 原生 Runtime 的缓存解包需要较短路径。模拟上游测试可将 `TEMP`、`TMP` 指向本项目 `logs/tmp`，避免系统临时目录叠加用户隔离路径后超过 Windows 原生模块的路径限制。

- 前端使用系统自己的聊天界面；`deepseek-harness-sdk` 通过 Python SDK 启动 DSH runtime。
- 对话正文和 Token 用量写入 `dsh_agent_conversations`、`dsh_agent_messages`，按系统用户 ID 隔离。
- DSH 自身以 JSONL 保存 runtime 会话状态；每个用户使用独立的 home/workspace，每个对话使用独立的 DSH session。删除对话时同时删除对应会话目录和数据库记录。
- 多轮对话优先恢复 DSH 会话。明确返回“会话不存在”时，使用当前用户在数据库中的最近成功问答重建上下文（最多 12 轮、48,000 字符），排除失败轮次；其他恢复错误直接报错，不重复调用模型。正常关闭给予 runtime 最多 15 秒等待时间，供其落盘。
- 回复通过 SSE 增量显示。Redis 使用 `DSH_AGENT_QUEUE_NAME` 独立命名空间调度，不占图片生成队列。
- 前端展示排队、runtime 启动、搜索/网页读取、回答阶段、来源链接和耗时。这些是 SDK 实际发出的运行事件，不包含模型的私有思维链；模型没有搜索时不会伪造搜索记录。
- 管理员可以查看用户、对话内容和 Token 用量；SDK 未提供人民币费用时不估算费用。

## 对话模型

聊天输入框旁可切换以下模型，选择按对话保存在数据库中。正在回复时不能切换；切换不新建对话，继续使用原会话上下文与用户隔离。历史消息保留各轮实际使用的模型。

| 前端名称 | 上游 model | 接口 |
| --- | --- | --- |
| GPT 5.6 Sol（原默认） | `tt-5.6-sol` | 保留现有 DSH Messages 路由 |
| GEM 3.5 Flash | `gem-3.5-flash` | Gemini `/v1beta/models/{model}:streamGenerateContent` |
| GEM 3.8 Flash | `gem-3.8-flash` | Gemini `/v1beta/models/{model}:streamGenerateContent` |
| GPT 6 Sol | `tt-6-sol` | `/v1/chat/completions` |
| GPT 6.1 Sol | `tt-6.1-sol` | `/v1/chat/completions` |
| DS-V4.1-Flash | `deepseek-v4.1-flash` | `/v1/chat/completions` |
| DS-V4-Flash | `deepseek-v4-flash` | `/v1/chat/completions` |

模型白名单与协议配置集中在 `backend/services/dsh_agent/models.py`，新模型复用 DSH 自带的 pi-ai 适配器、现有系统 Key 池和 DSH Redis 调度，不新增生图或视频生成服务。`DSH_AGENT_MODEL` 可配置新对话默认模型，旧默认保持 `tt-5.6-sol`。运行环境须包含 `@deepseek-ai/dsh-llm-pi-ai`。

### 上下文与自动压缩

- `DSH_AGENT_CONTEXT_WINDOW=128000` 是部署预算，不是上游公布容量。`DSH_AGENT_MODEL_CONTEXT_WINDOWS` 可提供按模型 ID 的 JSON 覆盖，例如 `{"gem-3.8-flash":65536}`，最小 32768。确认渠道容量后再调整。
- 复用 SDK 自带 `token-meter` 和 `compaction-basic`，不累加计费 Token 充当上下文。输入框显示 DSH 对当前有效历史、工具、图片的估算，草稿文字额外估算；待发送参考图发送后再计入。
- 压缩在请求前触发，阈值为 `min(窗口 * 0.8, 窗口 - 8192 输出预留 - 8192 余量)`。保留近期原文，摘要输出上限 4096 Token；明确上下文溢出时最多恢复一次。原始数据库聊天记录和 DSH 日志不删除。
- 切换模型按新预算显示，发送时由 DSH 原生模型切换检查决定是否压缩。原生估算含近似值，不能用作精确计费。
- `context-meter.mjs` 仅向 SDK 上报数字快照，不输出摘要或私有推理。快照随回复保存在已有 `usage_json` 中，API 单独返回 `context`，不污染计费字段，无需数据库迁移。
- 本机开发启动脚本使用 SDK 官方 `DSH_RUNTIME_MODE=node`。真实 runtime 的模拟上游回归可执行：`$env:DSH_RUNTIME_MODE='node'; $env:DSH_RUNTIME_SMOKE='1'; python -m pytest backend/test/test_dsh_agent_runtime.py -q`。不会请求真实模型。

模型选择位于输入框内部，输入组件独立在 `DshComposer.vue`。品牌图标使用 LobeHub Icons 的本地 SVG 资源，不依赖外部 CDN；来源 https://github.com/lobehub/lobe-icons ，MIT 许可随图标保存在 `assets/model-logos/LICENSE`。

用户消息靠右、助手回复靠左。页面高度限定在导航栏下方，输入框与会话标题保持固定，仅消息区滚动；向上阅读时暂停自动跟随回复。已有会话成功切换模型后，在时间顺序对应的位置显示“已从 A 切换至 B”。记录复用消息表的 `model_switch` 角色，内容为 `from_model` / `to_model` JSON，无需新增表或迁移，不进入 LLM 上下文、回复次数或 Token 统计；重复选择同一模型和失败切换不记记录。空白新对话选择的是初始模型，不生成切换记录。

`POST /api/dsh-agent/conversations` 和消息流请求可传 `model`；`PATCH /api/dsh-agent/conversations/{id}/model` 保存选择，返回原会话字段和 `model_switch`（本次切换记录或 null），仅允许会话所属用户操作。切换和记录在同一事务提交。空白或未支持的模型返回 400，正在回复返回 409。前端展示名中的 GPT 不改变上游请求字段 `tt-*`。

## 测试环境配置

`scripts/start-test-project.ps1` 将功能打开，并使用测试 Redis 的 `dsh_agent_jobs_test` 命名空间。当前限制为全局 4 个运行任务、每用户 1 个运行任务、每用户最多 4 个等待任务。模型沿用系统中转站配置，真实对话会产生上游费用。

生产默认 `DSH_AGENT_ENABLED=false`。部署时应独立配置 Redis 地址、队列命名空间和并发额度，并先运行数据库迁移 `052_dsh_agent` 至 `056_dsh_agent_message_feedback`。054 为现有对话表添加模型字段，055 新增图片与任务关联表，056 添加回复评价字段；旧对话保持兼容。runtime pool 上限按应用进程计算，不替代 Redis 调度并发限制；多实例必须使用共享持久化数据目录或确认 DSH 会话状态可跨实例恢复。

## 回复操作与停止

- 消息操作栏参考 assistant-ui Elements，以现有 Vue 组件实现，不引入 React。支持复制确认、互斥的赞/踩评价、重新回答和 Markdown 导出。评价通过 `PATCH /api/dsh-agent/conversations/{id}/messages/{message_id}/feedback` 保存，按消息所属用户鉴权。
- 蓝色呼吸点与耗时显示真实运行阶段；三点等待只在首段正文到达前出现。减少动态效果偏好下停用动画，不展示原始思维链。
- 流开始时返回 `user_message_id`。`POST /api/dsh-agent/conversations/{id}/stop` 必须带该 ID，数据库原子检查当前轮次并标记 `stopping`，旧页面的停止请求不会误停后续轮次。运行进程每秒观察数据库取消标记，排队中会撤出等待；SDK 未提供 session/cancel，因此通过 SDK 有界 shutdown 关闭该会话独占 runtime，结束后释放资源并保存 `stopped` 消息及已接收的正文。刷新后仍可继续或丢弃。
- 停止不代表上游退款，不取消已经提交的图片队列任务。继续与重新回答是新的文字轮次，携带 `text_only=true`，工具桥强制拒绝新增生图任务，避免重复生成和计费。页面离开只断开 SSE 读取，不自动停止后台运行。
- `DELETE /api/dsh-agent/conversations/{id}/messages/{message_id}/draft` 只允许丢弃本人的停止回复，从聊天历史隐藏，但保留数据库记录与已有 Token 用量供审计。已完成回复不可通过此接口丢弃。

## 工具与插件边界

企业模式关闭文件读写、终端、子进程、PTY、MCP 和用户自装插件，开放经过限制的 `web_search` / `web_fetch` 以及自建图片工具。搜索通过 loopback bridge 调用系统已有的 CowAgent 搜索服务，不把其密钥交给 DSH 子进程；只有现有搜索服务已配置且 `DSH_AGENT_SEARCH_ENABLED` 未关闭时才启用。搜索不可用时聊天仍可继续，但不会有网页搜索结果。不要把任意第三方插件直接开放给全员：插件通常拥有服务进程的权限。

## AgentTeams 插件安装

测试项目已接通协作模式。普通对话继续使用 `sdk-minimal`；输入框切换“多智能体协作”时，单独启用兼容宿主和团队 profile。前端复用现有 Vue 对话，不挂载插件的原生 Web 面板，不改生产服务。

配置统一放在 `backend/services/dsh_agent/plugins/profiles/agent-teams/`：

- `package.json`：锁定 `@nanmicoder/dsh-agent-teams@0.1.29` 与验证宿主 `@deepseek-ai/dsh@0.2.1-rc.2`。
- `pnpm-lock.yaml`：固定完整依赖图和下载完整性；`node_modules` 不提交到 Git。
- `cordis.patch.yml`：加载子 Agent 服务，最多两个成员、两名活动子 Agent；成员不能继续创建子 Agent。这不是跨进程的全局并发限制。
- `cordis.yml`：DSH 自动生成的空入口，不在这里编辑插件配置。

本机 Python SDK 是 `0.2.1rc2`，但其开发版 Node carrier 含 `0.2.2-rc.1` 组件，直接加载插件会被 retired-member guard 拒绝。验证宿主独立安装在上述目录，不改 `.venv`，也不跳过插件安全检查。用户数据不写入此配置目录；运行测试时使用独立临时 home/workspace。

在项目根目录安装或检查：

```powershell
.\backend\scripts\deployment\install_dsh_agent_teams.ps1
.\backend\scripts\deployment\install_dsh_agent_teams.ps1 -CheckOnly
```

安装脚本禁用依赖安装脚本，按锁文件恢复依赖；`-CheckOnly` 检查包版本和配置合成，不会发起模型请求，也不能替代运行时测试。真实 SDK + 本地模拟上游验收：

```powershell
$env:PYTHONPATH = 'backend'
$env:DSH_RUNTIME_SMOKE = '1'
.\.venv\Scripts\python.exe -m pytest backend/test/test_dsh_agent_plugins.py -q
```

测试检查团队工具实际注册、文件/终端工具仍然关闭、待确认计划落盘且不启动成员。不会调用真实中转站。完整集成回归在同一命令中加入 `backend/test/test_dsh_agent_teams.py`，验证真实插件的计划、批准、成员执行、Token 汇总及停止。

### 协作运行与隔离

- `teams.py` 管理用户/会话独立工作目录、只读计划快照、确认校验和执行生命周期。团队 home 位于 `data/dsh-agent/teams-home/users/<用户哈希>`，workspace 位于 `data/dsh-agent/teams-workspace/users/<用户哈希>/<会话ID>`。不使用共享验证目录；现有普通聊天数据保留。
- 消息流支持 `mode=normal|teams`，默认普通对话。确认、取消、继续通过 `team_action=approve|discard|resume` 和 `team_revision` 传入，仍复用原来的数据库原子轮次认领。`GET /api/dsh-agent/conversations/{id}/team` 只允许所属用户读取，无模型请求。
- 所有创建必须等待确认；模型自行调用批准、自动批准、成员越权批准均被 `team-policy.mjs` 的工具执行策略拒绝。确认绑定用户看到的计划版本，计划变化返回 409。修改计划通过对话提意见，工具修改原计划后再次确认。
- 父任务继续使用 Redis 全局4、单用户1、等待4的调度。独立请求调度命名空间追加 `:team_requests`，主 Agent 和成员的模型请求合计全局8、单用户3，最多2成员、递归深度0、每次协作最多16任务、每轮最多64次模型请求，整体最长15分钟。不是给每个员工分别开放8并发。
- 搜索复用现有本机 `SearchBridge`。普通模式继续使用原配置字段，协作模式在 `team-gateway.patch.yml` 适配独立宿主的 `baseURL/apiKeyEnv`；搜索未配置时使用不可达的本机地址，绝不回退到 DeepSeek 官方地址或使用聊天 Key 搜索。协作搜索关闭 `sendOriginalQuestion`，不额外发送整条用户提问。
- 每轮每个成员最多6次搜索工具调用、12次网页读取，同一查询或网址最多2次。401/403、搜索参数或服务配置错误立即结束；临时错误后的再次调用按500/1000ms退避，连续失败3次停止。鉴权错误、失败次数和预算都由执行策略检查，不依赖提示词；不自动重新提交模型请求或图片任务。
- 第56次模型请求开始进入收尾预算，禁止新增搜索、网页读取、任务和生图，只允许报告和完成/失败已有任务；64次硬上限不变。无法完成时返回受控错误并清退成员，不能无限重试。
- 前置任务失败后的后续任务通过只读快照投影为 `blocked`（支持多级依赖），不直接修改插件状态文件。无可执行任务时立即结束等待，调用插件原生停止接口持久化收尾；前端显示“协作未完成 / 前置任务失败，未执行”。错误原因、团队快照和已产生的用量保留在原消息中，刷新后仍可查看；不会悄悄切换到未经联网核验的方案。
- `team_gateway.py` 仅监听 loopback，凭会话令牌转发白名单对话端点；所有成员沿用当前所选模型与现有密钥池选中的凭证。真实上游密钥不交给团队运行进程。每次响应通过现有 `capture_chat_response` 保存 `X-Task-Id`、系统用户和用户轮次，来源 `dsh_agent_teams`，继续走已有费用对账；上游缺少费用/任务ID时不虚构金额。
- SDK订阅覆盖父子会话。主 Agent 暂时 idle 不会提前释放父任务槽位、工具或费用授权；直到任务终态且成员与主 Agent 静止后完成。Token 计入主/子会话；中断时保留已接收的用量。计划快照复用消息 `usage_json`，API单独投影 `team`，不需要新表。
- 停止先调用插件原生 `haltTeamWork`，持久化停止并清退成员，然后有界关闭当前 runtime。已经提交的图片任务及上游费用不会回滚。离开网页不自动取消；后台继续受轮次租约与调度控制。
- `DshTeamPanel.vue` 展示成员、任务状态和结果，提供确认、修改、取消和继续操作；SSE增量更新，重新打开会话通过鉴权接口恢复。刷新后若任务仍在运行，每3秒查询轻量状态，隐藏页面暂停、失败退避，仅结束后重新加载一次消息，支持停止后台任务。不持续轮询全量历史，也不展示内部推理链。
- 生图继续走现有图片队列和幂等链路；成员工具调用用会话ID和原生callId组合生成稳定标识，避免不同成员的同名callId冲突。
- 工具限制与目录隔离不是OS级沙箱。多实例部署仍需共享持久卷或会话粘性；本次是测试环境接入，不代表已完成生产容量验收。

## DSH 图片工具

- `image-tools.mjs` 原生注册 `generate_image`、`get_image_task`，由 DSH 的工具调用机制选择执行，不使用关键词分流。聊天模型与生图模型独立选择。
- 生图模型复用 GPT Image 2、2.5 Flare/Sunburst、2.6 Flare/Sunburst、Banana 2。参数白名单复用现有中转适配器；2.6 仅使用原生比例，不传 1K/2K/4K 档位。
- 输入框内直接切换聊天与生图模型，不再提供独立生图参数面板。前端只传所选生图模型，比例、数量等参数由 DSH 优先遵循用户要求，未指定则按用途在模型白名单内选择；明确要求不受支持时先询问，不静默替换。
- 参考图片支持文件选择、在输入框内粘贴图片和拖拽文件到输入框；三种入口复用同一上传校验，每次最多 6 张、每张 20MB，仅支持 PNG/JPEG/WebP/GIF，普通文本粘贴不受影响。
- 图片上传 `POST /api/dsh-agent/conversations/{id}/images`；移除未发送图片使用同路径 `DELETE /{media_id}`。每轮最多 6 张真实图片，每张不超过 20MB、4000 万像素，格式 PNG/JPEG/WebP/GIF。草稿与已发送图片、生成任务均按用户和会话隔离，上传绑定与用户消息在同一事务提交。
- 图片任务关联写入 `dsh_agent_media`。原生 `callId` 生成稳定的任务 ID，同一轮相同请求也去重。单次 1-4 张，每轮最多 8 张；不确定的提交不自动重发，该轮停止新增任务。删除对话不会删除生图历史或费用账本。
- 工具只经过当前会话和当前轮次授权的 loopback bridge 提交既有 `image_task_service`，记录 `source=dsh`、系统用户、会话和轮次，继续走现有图片 Redis 队列、Worker、Key 池、图库和费用投影。不单独创建生图队列或费用账本。
- 工具立即返回任务状态，前端只批量查询未完成任务 `POST /api/dsh-agent/conversations/{id}/images/query`，无重叠轮询，隐藏页暂停，失败指数退避。历史详情恢复图片和任务；生成图可通过 `media_id:图片下标` 继续编辑。下载复用已鉴权的图片任务 ZIP 接口。
- 生图结果在对应助手回复内展示，数据库仍绑定原用户轮次，不改变工具幂等标识。点图使用原生对话框放大、缩放和切换，下载是单独操作；Esc 关闭并恢复焦点。
- GPT/GEM 的对话路由声明图片输入；预览图片缩小到 1280px 内再交给聊天模型，生图模型仍接收原始参考图片。DS 路由尚未确认视觉能力，保持文本输入，但仍可调用工具把原图交给生图模型。
- 测试命令：`DSH_RUNTIME_MODE=node DSH_RUNTIME_SMOKE=1 python -m unittest backend.test.test_dsh_agent_images backend.test.test_dsh_agent_runtime`（需设置后端 PYTHONPATH）；前端运行 `npm run test:e2e -- e2e/dsh-agent.spec.ts e2e/dsh-images.spec.ts`。本地模拟上游验证，不产生真实生成费用。

## 电商设计专家

- 从输入框“＋ → 专家 → 电商设计专家”启用，可通过输入框内的标签移除。与对话/生图模型选择独立；新对话不继承专家，默认对话模型仍为 GEM 3.5 Flash。
- 请求仅提交白名单 `expert_id=ecommerce-design`，不接受浏览器提供的系统提示词或本地路径。每轮选择保存在已有消息 `usage_json`，API 单独投影 `expert_id`，不计入 Token 用量，不需要新表或迁移。刷新后恢复该对话最近一轮使用的专家，未发送的选择不写数据库。
- `experts/ecommerce_design/SKILL.md` 为服务端专家策略；`references/product-rules.md` 为商品一致性约束，`references/style-library.md` 为五种电商图片用途与构图规则。`read_expert_reference` 只允许当前轮次读取这两份资料，不开放文件系统、终端或任意 URL。
- 专家策略加入 runtime 缓存指纹；选择/移除专家或系统规则变化会更换 runtime 并恢复同一会话，避免复用旧系统角色。商品事实、已提交任务仍保留。
- 明确要求生成/改图时复用 `generate_image`；纯建议、分析和写提示词只对话。模型自主决策遵循规则，不等于对所有语义提供确定性保证。商品身份优先于风格，未指定改变比例时跟随原图，不支持的比例先询问。生成质量仍需人工验收。
- 无新队列、账本、向量库或插件安装。`source.json` 记录参考项目和固定版本，仅借鉴分类/风格/场景选择及按需资料组织思路，不打包第三方图库与案例提示词。

## 独立技能与连接器

- “＋”菜单分开提供专家、技能、连接器，原图片、文件和协作功能保留。技能由用户独立手动选择；选择、切换或移除专家不会自动启用或取消技能，新对话不继承选择。
- `skills/gpt-image-2-style-library/` 独立保存上游 Skill、结构化风格参考、MIT 许可证和来源版本。不打包第三方图库、完整案例提示词或可执行脚本，不安装 npm 全局包。维护链接和命令只作来源信息，不自动执行。升级时按 `source.json` 固定版本审核替换。
- 消息流接收白名单 `skill_ids`；省略字段、`null` 或 `[]` 表示未手动选择技能，GPT Image 设计库仍必须显式选择后才授权。图表设计可由模型在用户提出绘图需求时按需读取，不绑定专家、不自动勾选。选择通过现有 `usage_json` 保存，API 单独返回 `skill_ids`，不混入费用/Token 数值，无新表或迁移。只保存已发送轮次的选择。
- `read_skill` 通过现有会话授权桥读取本轮授权的技能和白名单参考。不能读取其他路径、任意 URL 或其他用户会话。技能开关与工具插件内容加入 runtime 策略指纹。对话展示真实资料读取和图示保存活动，不展示内部推理链。
- 技能提供设计参考，商品一致性、用户要求和实际模型参数优先。实际生图仍走已有 `generate_image`、队列、存储和费用记录，不新增生成链路。
- 连接器当前仅提供已有公开网页搜索与来源读取；未配置时禁用入口。不代表已连接企业知识库、企业微信或任意 MCP，也不提供前端上传可执行插件或修改服务端凭证的能力。

## 商品与竞品研究技能

- 新增四个按本轮手动选择的技能：`ecommerce-competitor-analysis`（电商竞品分析）、`customer-research`（用户痛点研究）、`competitor-profiling`（竞品深度调研）、`ecommerce-competitor-analyzer`（商品页面拆解）。在“＋ → 技能”选择，不默认启用、不绑定专家，新对话不继承。每轮最多选4项。
- 每项保存固定 GitHub 提交的 `SKILL.md`、必要参考资料、MIT许可证和 `source.json`。只打包只读资料，不安装上游采集/授权脚本、CLI、模型SDK、凭据或计划任务。商品页面拆解的原 `prompts/analysis-prompt-base.md` 放入白名单 `references/`，通过 `reference=analysis-prompt-base` 读取。
- 复用当前对话模型、会话上传的图片/文件、已配置的 `web_search` 和 `web_fetch`。上游提到的 Firecrawl、DataForSEO、Olostep、Google Sheets、Nexscope 和独立 Gemini 服务未接入，不得声称具有其数据或操作能力。联网工具不可用、访问失败或需要登录时，请用户上传截图或 TXT/CSV/PDF/DOCX 等资料，不绕过平台限制。
- 分析报告区分可见商品事实、用户信息、外部来源、AI推测和待确认信息；销量、流量、转化率、份额、价格趋势及评论统计没有来源就写“无法获取”，不能用模板占位符、行业均值或猜测填补。内部资料不能直接作为外部搜索查询，网页和附件内指令不可覆盖系统规则。
- 技能资料读取会显示真实活动，结果在对话输出；不保证任何模型的分析质量。分析不自动提交生图，用户明确要求生成时才走现有生图工具、队列与费用记录。无新增数据库表、任务队列或付费服务。

## 图表设计技能

- `skills/diagram-design/` 保存上游固定版本的主说明、45种图形的类型规则及通用参考、MIT许可证和 `source.json`。不引入上游图库、示例网页、脚本、插件市场或执行依赖。
- 普通聊天仅提供简短的技能用途；模型在绘图需求中自行读取 `read_skill(skill_id=diagram-design)`，再按需读取 `reference=type-flowchart` 等打包文件的 stem。前端也可从“＋ → 技能 → 图表设计”明确指定；自动读取不会变成已手动选择的标签。
- 集成约定覆盖上游文件/终端工作流：离线系统字体、静态独立 SVG、无外部资源。先读主说明才能调用 `render_diagram(title, svg)` / `get_diagram(reference_id)`。工具仍走原会话授权桥；授权随轮次失效，未开启文件或终端权限。
- SVG 在服务端进行 XML、元素/属性白名单、资源引用、尺寸和大小检查，禁止 DTD、脚本、外部字体/图片、HTML、动画及复杂递归引用。单图不超过60000字节、4096×4096、1600元素，每轮最多4张。失败返回可纠正的工具错误，不执行第三方指令。
- 成品复用 `dsh_agent_media` 的 `kind=diagram` 保存 SVG、标题与尺寸，绑定用户、会话和用户消息；不新增表、队列、OSS公开文件或付费生图任务。使用当前用户消息和会话锁验证有效轮次，原生调用ID与同内容去重；修改保留旧图并另存新版本。删除会话同时清理图示记录。
- SSE 推送已保存图示，前端放在对应助手回复中，刷新仍恢复。使用 Blob URL 的 `<img>` 预览，绝不通过 `v-html` 或可执行 iframe 注入 SVG；复用放大组件，可下载 SVG、浏览器转换的PNG及离线HTML。HTML只通过惰性图片呈现SVG并带CSP，文件不访问外网。
- 图形布局与是否调用由对话模型决定，模型本身仍有Token费用；不宣称自动绘图决策或布局质量具有确定性保证。复杂图按上游规则拆分，人工确认内容和数据后再用于发布。

## 文档附件与会话导航

- 输入框支持 TXT、MD、CSV、JSON、LOG、PDF、DOCX，单文件最多 5MB，每轮最多 4 个文件，可与最多 6 张参考图一起发送；支持选择、粘贴、拖拽及上传失败提示。仅附件也可发送。
- `POST /api/dsh-agent/conversations/{id}/files` 验证所属用户并提取文字；`DELETE /files/{media_id}` 仅移除本人的未发送附件。消息流用 `file_ids` 绑定，和消息认领在同一事务提交，不复用图片引用字段。
- 文本最多保留 12,000 字符，截断在界面和模型输入中明确说明。PDF 最多 100 页，不支持加密或纯扫描件；DOCX 读取段落及表格，不执行宏，不开放系统文件访问。
- 复用 `dsh_agent_media` 保存附件元数据和提取文字，无需新表。原始文件不持久化，不提供原文件下载；提取正文不在历史 API 公开返回，只交给所属会话的模型。文档不会混入生图工具的图片目录。
- 搜索来源折叠为“来源 N”，按安全 HTTP(S) URL 去重，点击展开真实来源，Esc 或点击外部收起。历史侧栏按时间分组；消息区滚动条变细，桌面对话导航按用户轮次显示刻度，可悬停预览、点击定位，窄屏隐藏。
