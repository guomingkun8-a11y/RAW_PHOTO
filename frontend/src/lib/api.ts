import { httpRequest, request } from "@/lib/request";
import { webConfig } from "@/lib/config";
import { getStoredAuthKey } from "@/stores/auth";

export type ImageModel = string;
export type AuthRole = "admin" | "user";

export type ImageTaskImageOptions = {
  aspectRatio?: string;
  imageSize?: string;
  thinkingLevel?: string;
  imageCount?: number;
  source?: "standard" | "canvas";
  canvasUnits?: number;
};

export type Model = {
  id: string;
  object: string;
  created: number;
  owned_by: string;
  permission: unknown[];
  root: string;
  parent: string | null;
};

type ModelListResponse = {
  object: string;
  data: Model[];
};

export type OpenAIRelaySettings = {
  enabled?: boolean;
  base_url?: string;
  api_key?: string;
  api_keys?: string[];
  has_api_key?: boolean;
  api_key_count?: number;
};

export type VideoGenerationDuration = number | "auto";

export type VideoGenerationModelSpec = {
  id: string;
  label: string;
  modes: Array<"text_to_video" | "image_to_video" | string>;
  min_images: number;
  max_images: number;
  image_input_kind?: "none" | "reference" | "first_last_frame" | string;
  aspect_ratios: string[];
  durations: VideoGenerationDuration[];
  default_duration: VideoGenerationDuration;
  option_key: "resolution" | "mode" | string;
  options: string[];
  default_option: string;
};

export type SettingsConfig = {
  base_url?: string;
  openai_relay?: OpenAIRelaySettings;
  image_task_queue?: {
    enabled?: boolean;
    executor?: string;
    owner_concurrency?: number | string;
    dynamic_owner_concurrency_enabled?: boolean;
    dynamic_owner_concurrency_threshold?: number | string;
    dynamic_owner_concurrency_max?: number | string;
    owner_pending_limit?: number | string;
    [key: string]: unknown;
  };
  image_retention_days?: number | string;
  image_poll_timeout_secs?: number | string;
  image_timeout_retry_secs?: number | string;
  image_storage?: {
    enabled: boolean;
    mode: "local" | "webdav" | "minio" | "both";
    provider?: "webdav" | "minio";
    public_base_url: string;
    webdav_url?: string;
    webdav_username?: string;
    webdav_password?: string;
    webdav_root_path?: string;
    minio_endpoint?: string;
    minio_access_key?: string;
    minio_secret_key?: string;
    minio_bucket?: string;
    minio_region?: string;
    minio_secure?: boolean;
    minio_root_path?: string;
    [key: string]: unknown;
  };
  image_reference_upload?: {
    enabled?: boolean;
    provider?: "oss";
    [key: string]: unknown;
  };
  video_upload?: {
    enabled?: boolean;
    provider?: "oss";
    [key: string]: unknown;
  };
  video_analysis?: {
    enabled?: boolean;
    queue_enabled?: boolean;
    worker_concurrency?: number | string;
    total_concurrency?: number | string;
    owner_concurrency?: number | string;
    owner_pending_limit?: number | string;
    [key: string]: unknown;
  };
  video_generation?: {
    enabled?: boolean;
    base_url?: string;
    queue_enabled?: boolean;
    has_api_key?: boolean;
    api_key_count?: number;
    credential_source?: string;
    submit_path?: string;
    status_path?: string;
    models?: VideoGenerationModelSpec[];
    worker_concurrency?: number | string;
    total_concurrency?: number | string;
    owner_concurrency?: number | string;
    owner_pending_limit?: number | string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type ImageTask = {
  id: string;
  status: "queued" | "running" | "success" | "error" | "canceled";
  mode: "generate" | "edit";
  model?: ImageModel;
  size?: string;
  quality?: string;
  created_at: string;
  updated_at: string;
  conversation_id?: string;
  product_id?: number;
  template_id?: number;
  data?: Array<{
    b64_json?: string;
    url?: string;
    storage_rel?: string;
    revised_prompt?: string;
    width?: number;
    height?: number;
    requested_size?: string;
    aspect_ratio_corrected?: boolean;
    resolution_corrected?: boolean;
  }>;
  error?: string;
  progress?: string;
  elapsed_secs?: number;
  duration_ms?: number;
  cost?: number;
  upstream_task_id?: string;
  stage_timings_ms?: {
    upload?: number;
    queue?: number;
    generation?: number;
    save?: number;
  };
  batch_id?: string;
  batch_index?: number;
  batch_total?: number;
  batch_progress?: {
    batch_id: string;
    total: number;
    completed: number;
    failed: number;
    canceled: number;
    running: number;
    queued: number;
  };
};

export type VideoGenerationTask = {
  id: string;
  owner_id?: string;
  owner_name?: string;
  owner_username?: string;
  status: "queued" | "running" | "success" | "error" | "canceled";
  mode: "text_to_video" | "image_to_video" | string;
  model?: string;
  prompt?: string;
  aspect_ratio?: string;
  duration_secs?: VideoGenerationDuration;
  quality?: string;
  resolution?: string;
  image_urls?: string[];
  created_at: string;
  updated_at: string;
  data?: Array<{
    type?: "video" | string;
    url?: string;
    cover_url?: string;
  }>;
  video_url?: string;
  cover_url?: string;
  source_video_url?: string;
  storage?: string;
  storage_rel?: string;
  file_size?: number;
  storage_error?: string;
  error?: string;
  progress?: string;
  elapsed_secs?: number;
  duration_ms?: number;
  cost?: number;
  upstream_task_id?: string;
  cancellation_pending?: boolean;
  reconciliation_required?: boolean;
  conversation_id?: string;
  turn_id?: string;
};

export type VideoGenerationTaskListResponse = {
  items: VideoGenerationTask[];
  missing_ids: string[];
  has_more?: boolean;
  limit?: number;
  total?: number;
  next_cursor?: string | null;
};

export type VideoAgentAttachment = {
  kind: "image" | "video" | "generation";
  name?: string;
  mime_type?: string;
  url?: string;
  size?: number;
  sha256?: string;
  video_id?: string;
  analysis_status?: string;
  analysis_error?: string;
  task_id?: string;
  model?: string;
  prompt?: string;
  duration_secs?: VideoGenerationDuration;
  resolution?: string;
};

export type VideoAgentPlan = {
  id: string;
  status: "completed" | string;
  message: string;
  prompt: string;
  analysis_error?: string;
  reasoning_summary?: string;
  reasoning_enabled?: boolean;
  chat_model?: string;
  duration_ms?: number;
  conversation_id?: string;
  turn_id?: string;
  owner_id?: string;
  owner_username?: string;
  owner_name?: string;
  attachments?: VideoAgentAttachment[];
  created_at: string;
};

export type VideoAgentPlanListResponse = {
  items: VideoAgentPlan[];
  total: number;
  limit: number;
  has_more: boolean;
};

export type VideoAgentPlanStreamEvent =
  | { type: "reasoning.delta"; delta: string }
  | { type: "answer.delta"; delta: string }
  | { type: "pending"; message: VideoAgentPlan }
  | { type: "completed"; message: VideoAgentPlan }
  | { type: "error"; message: string };

export type AgentRunStatus = "pending" | "running" | "waiting_for_images" | "waiting_for_input" | "completed" | "failed" | "canceled";

export type AgentEvent = {
  sequence: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type AgentStep = {
  stepId: string;
  index: number;
  action: "call_tool" | "finish" | "ask_user" | "fail";
  status: "running" | "completed" | "failed" | "waiting_for_input";
  title: string;
  toolName?: string;
  startedAt: string;
  finishedAt?: string;
  durationMs?: number;
  error?: string;
};

export type ImageAgentQualityCheck = {
  imageIndex: number;
  pageId?: string;
  pageTitle?: string;
  status: "passed" | "review" | "failed";
  score: number;
  summary: string;
  issues: string[];
  suggestions: string[];
  model?: string;
};

export type ImageAgentProposalPage = {
  id: string;
  title: string;
  purpose: string;
  visualDirection?: string;
  scene?: string;
  composition?: string;
  lighting?: string;
  background?: string;
  prompt: string;
  needsTypography: boolean;
};

export type ImageAgentCreativeBrief = {
  scene?: string;
  background?: string;
  composition?: string;
  camera?: string;
  lighting?: string;
  palette?: string;
  props?: string;
  typography?: string;
  visualHook?: string;
};

export type ImageAgentAdvisor = {
  assistantMessage: string;
  suggestions: string[];
  intent?: string;
  recommendedAction?: string;
  creativeBrief?: ImageAgentCreativeBrief;
  knowledgeSources?: Array<{ id: string; title: string }>;
  memorySources?: AgentMemorySource[];
  memoryUpdates?: AgentMemoryUpdate[];
};

export type AgentMemoryScope = "user" | "brand" | "project" | "conversation";

export type AgentMemoryUpdate = {
  memoryId: string;
  content: string;
  category: string;
  scope: AgentMemoryScope;
  scopeId?: string;
};

export type AgentMemorySource = {
  id: string;
  title: string;
  scope?: AgentMemoryScope;
  category?: string;
};

export type AgentMemoryItem = {
  id: number;
  memoryId: string;
  scope: AgentMemoryScope;
  scopeId: string;
  memoryKey: string;
  category: string;
  content: string;
  sourceConversationId?: string;
  sourceRunId?: string;
  confidence: number;
  confirmed: boolean;
  status: string;
  supersedesId?: number | null;
  createdAt: string;
  updatedAt: string;
  lastUsedAt?: string;
  metadata?: Record<string, unknown>;
  sourceMessageIds?: Array<string | number>;
};

export type AgentMemoryCounts = {
  active: number;
  pendingReview: number;
};

export type ImageAgentProposal = {
  title: string;
  summary: string;
  creativeConcept?: string;
  visualHook?: string;
  imageCount: number;
  pages: ImageAgentProposalPage[];
  strategyText?: string;
  assistantMessage?: string;
  suggestions?: string[];
  creativeBrief?: ImageAgentCreativeBrief;
  folderId?: string;
  folderSummary?: Record<string, unknown>;
  batchPlanId?: string;
};

export type ImageAgentResult = {
  phase?: "awaiting_confirmation" | "completed" | string;
  promptPlan: Record<string, unknown> & {
    model?: string;
    sceneType?: ProfessionalSceneType;
    sceneName?: string;
    finalPrompt?: string;
    negativePrompt?: string;
    productProfile?: { productName?: string };
    editIntent?: ProfessionalEditIntent;
    subjectMutationPolicy?: SubjectMutationPolicy;
    changedAttributes?: string[];
    needsTypography?: boolean;
    needsClarification?: boolean;
    clarificationQuestion?: string;
    resolvedSize?: string;
  };
  turnIntent?: {
    intent?: string;
    originalIntent?: string;
    confidence?: number;
    reason?: string;
    source?: string;
  };
  proposal?: ImageAgentProposal;
  images: Array<{
    taskId: string;
    pageId?: string | null;
    pageTitle?: string | null;
    purpose?: string | null;
    b64_json?: string | null;
    url?: string | null;
    revised_prompt?: string | null;
    width?: number | null;
    height?: number | null;
    requestedSize?: string | null;
    aspectRatioCorrected?: boolean;
    cost?: number | null;
  }>;
  qualityChecks: ImageAgentQualityCheck[];
  revisionCount: number;
  assistantMessage?: string;
  suggestions?: string[];
  intent?: string;
  recommendedAction?: string;
  creativeBrief?: ImageAgentCreativeBrief;
  knowledgeSources?: Array<{ id: string; title: string }>;
  memorySources?: AgentMemorySource[];
  memoryUpdates?: AgentMemoryUpdate[];
  longTermMemoryEnabled?: boolean;
  optimizationRoute?: "direct_consult" | "confirmed_generation" | "full_agent" | string;
  agentExecutionProfile?: string;
  modelUsage?: {
    dialogueCalls: number;
    visionCalls: number;
    totalCalls: number;
    imageGenerationCalls: number;
    estimatedInputChars?: number;
    estimatedOutputChars?: number;
  };
  folderId?: string;
  batchPlanId?: string;
  folderSummary?: Record<string, unknown>;
  batchProgress?: { total: number; completed: number; failed: number };
  batchItems?: AgentBatchPlanItem[];
};

export type AgentRun = {
  runId: string;
  agent: string;
  status: AgentRunStatus;
  startedAt: string;
  finishedAt?: string;
  durationMs?: number;
  maxSteps: number;
  stepCount: number;
  toolCalls: number;
  steps: AgentStep[];
  events?: AgentEvent[];
  error?: string;
  waitingForInput?: string;
  result?: ImageAgentResult;
};

export type AgentFolderItem = {
  id: number;
  folderId: string;
  relativeName: string;
  name: string;
  type: string;
  size: number;
  width: number;
  height: number;
  category: string;
  storageRel?: string;
  url: string;
  analysis?: { text?: string; question?: string; updatedAt?: string; [key: string]: unknown };
  status: string;
};

export type AgentFolderAsset = {
  folderId: string;
  ownerId?: string;
  conversationId?: string;
  name: string;
  status: string;
  itemCount: number;
  totalBytes: number;
  summary: {
    fileCount?: number;
    totalBytes?: number;
    categories?: Record<string, number>;
    mimeTypes?: Record<string, number>;
    dimensions?: Record<string, number>;
    samples?: Array<{ id: number; name: string; category: string; width: number; height: number; url: string }>;
    [key: string]: unknown;
  };
  items?: AgentFolderItem[];
  createdAt?: string;
  updatedAt?: string;
};

export type AgentBatchPlanItem = {
  id: number;
  folderItemId: number;
  index: number;
  name: string;
  category: string;
  title: string;
  purpose: string;
  taskId?: string;
  status: string;
  attempts: number;
  error?: string;
};

export type AgentBatchPlan = {
  planId: string;
  folderId: string;
  status: string;
  totalItems: number;
  completedItems: number;
  failedItems: number;
  summary?: Record<string, unknown>;
  items: AgentBatchPlanItem[];
  updatedAt?: string;
};

export type ImageLibraryItem = {
  id: number;
  task_id: string;
  owner_id: string;
  mode: "generate" | "edit";
  model?: ImageModel;
  prompt?: string;
  revised_prompt?: string;
  product_id?: number;
  template_id?: number;
  created_by?: string;
  size?: string;
  quality?: string;
  image_rel: string;
  image_url: string;
  thumbnail_url?: string;
  width?: number;
  height?: number;
  file_size?: number;
  storage?: string;
  duration_ms?: number;
  favorite?: boolean;
  reference_images?: MonitoringTaskReferenceImage[];
  deleted_at?: string;
  created_at: string;
};

export type ImageLibraryResponse = {
  items: ImageLibraryItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  next_cursor: ImageLibraryCursor | null;
};

export type ImageLibraryCursor = {
  created_at: string;
  id: number;
};

export type ProductReference = {
  id: number;
  product_id: number;
  file_name?: string;
  mime_type?: string;
  image_rel: string;
  image_url: string;
  thumbnail_url?: string;
  width?: number;
  height?: number;
  file_size?: number;
  storage?: string;
  created_at: string;
};

export type BusinessProduct = {
  id: number;
  name: string;
  sku?: string;
  brand?: string;
  category?: string;
  selling_points?: string;
  notes?: string;
  status: "active" | "archived" | string;
  references: ProductReference[];
  cover_image_url?: string;
  created_at: string;
  updated_at: string;
};

export type PromptTemplate = {
  id: number;
  name: string;
  category: string;
  content: string;
  owner_id?: string;
  owner_name?: string;
  can_manage?: boolean;
  created_at: string;
  updated_at: string;
};

export type ImagePromptAnalysisAction = "suggest" | "optimize" | "enhance";

export type ImagePromptAnalysisResponse = {
  model: string;
  analysis: {
    subject?: string;
    materials?: string;
    style?: string;
    composition?: string;
    textLogo?: string;
    risks?: string;
  };
  suggestions: string[];
  suggestionPrompt: string;
  optimizedPrompt: string;
  negativePrompt: string;
};

export type PromptEngineMode = "standard" | "professional" | "general";
export type ProfessionalAgentEngine = "cowagent";

export type ProfessionalEditIntent =
  | "scene_edit"
  | "visual_style_edit"
  | "product_attribute_edit"
  | "product_replace"
  | "generate_new"
  | "ambiguous";

export type SubjectMutationPolicy = "preserve" | "mutate_requested_attributes" | "replace";

export type ProfessionalSceneType =
  | "auto"
  | "taobao_text_main"
  | "white_background"
  | "lifestyle"
  | "material_macro"
  | "poster_banner"
  | "social_cover"
  | "magazine_editorial"
  | "luxury_atmosphere";

export type AuditLogItem = {
  id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id?: string;
  detail?: string;
  created_at: string;
};

export type SystemAnnouncement = {
  id: number;
  title: string;
  content: string;
  type: "info" | "success" | "warning" | "error" | string;
  enabled: boolean;
  created_by?: string;
  created_at: string;
  updated_at: string;
};

export type RealtimeAnnouncementEvent = SystemAnnouncement;

export type RealtimeImageTaskProgressEvent = {
  task_id: string;
  state: "queued" | "running" | "success" | "error" | "canceled" | string;
  status?: string;
  status_group?: string;
  is_final?: boolean;
  progress?: string;
  result_url?: string;
  result_type?: string;
  error?: string;
  cost?: number;
  model?: string;
  conversation_id?: string;
  turn_id?: string;
  updated_at?: string;
};

export type RealtimeEvent =
  | { type: "announcement"; data: RealtimeAnnouncementEvent }
  | { type: "image_task_progress"; data: RealtimeImageTaskProgressEvent };

type ImageTaskListResponse = {
  items: ImageTask[];
  missing_ids: string[];
};

export type ImageConversationApiPayload = Record<string, unknown> & {
  id?: string;
  title?: string;
  createdAt?: string;
  updatedAt?: string;
  turns?: unknown[];
};

export type ImageConversationCursor = {
  updated_at: string;
  id: string;
};

export type ImageConversationListResponse = {
  items: ImageConversationApiPayload[];
  total: number;
  limit?: number;
  has_more?: boolean;
  next_cursor?: ImageConversationCursor | null;
};

export type LoginResponse = {
  ok: boolean;
  version: string;
  role: AuthRole;
  subject_id: string;
  username?: string;
  name: string;
  avatar_url?: string;
  token: string;
};

export type CurrentUserResponse = {
  ok: boolean;
  role: AuthRole;
  subject_id: string;
  username?: string;
  name: string;
  avatar_url?: string;
};

export type CaptchaResponse = {
  ok: boolean;
  captcha_id: string;
  image_data_url: string;
  expires_in: string;
};

export type UserAccount = {
  id: string;
  username: string;
  name: string;
  role: AuthRole;
  enabled: boolean;
  protected?: boolean;
  created_at: string;
  updated_at: string;
  last_login_at?: string;
  avatar_url?: string;
};

export type MonitoringUserStat = {
  user_id: string;
  username: string;
  name: string;
  role: AuthRole | "unknown";
  enabled: boolean;
  online: boolean;
  active_sessions: number;
  success_count: number;
  failed_count: number;
  total_count: number;
  cost_total?: number;
  cost_count?: number;
  cost_average?: number;
  queued_tasks: number;
  running_tasks: number;
  active_tasks: number;
  last_login_at?: string;
  last_seen_at?: string;
};

export type MonitoringSource = "image" | "video" | "audio";

export type MonitoringQueueItem = {
  queue: string;
  label: string;
  waiting: number;
  running: number;
  failed: number;
  p95_ms: number;
  available: boolean;
  concurrency?: number;
  updated_at?: string;
  error?: string;
};

export type MonitoringQueuesResponse = {
  items: MonitoringQueueItem[];
  updated_at?: string;
};

export type MonitoringQueueOwnerActivity = {
  owner_id: string;
  queued_tasks: number;
  running_tasks: number;
  active_tasks: number;
};

export type MonitoringQueueSummary = {
  enabled: boolean;
  executor: string;
  queue_depth: number;
  queue_depths?: { standard?: number; agent?: number; batch?: number };
  queued_tasks: number;
  running_tasks: number;
  stale_running_tasks: number;
  active_slots: number;
  slot_limit: number;
  adaptive_concurrency?: {
    enabled: boolean;
    configured_limit: number;
    effective_limit: number;
    minimum_limit?: number;
    recovery_successes?: number;
    recovery_target?: number;
    cooldown_remaining_secs?: number;
  };
  active_workers: number;
  worker_concurrency: number;
  local_concurrency_limit: number;
  postprocess_concurrency?: number;
  async_postprocess_enabled?: boolean;
  configured_total_concurrency: number;
  total_concurrency: number;
  owner_concurrency: number;
  effective_owner_concurrency: number;
  dynamic_owner_concurrency_enabled: boolean;
  dynamic_owner_concurrency_threshold: number;
  dynamic_owner_concurrency_max: number;
  active_owner_count: number;
  owner_pending_limit: number;
  stale_running_timeout_secs: number;
  worker_heartbeat_secs: number;
  owner_activity?: MonitoringQueueOwnerActivity[];
};

export type MonitoringAgentQueueSummary = {
  enabled: boolean;
  available?: boolean;
  error?: string;
  queue?: string;
  lag?: number;
  pending?: number;
  active?: number;
  activeWorkers?: number;
  workerConcurrency?: number;
  totalConcurrency?: number;
  ownerConcurrency?: number;
  ownerPendingLimit?: number;
};

export type MonitoringVideoGenerationQueueSummary = {
  enabled: boolean;
  available?: boolean;
  error?: string;
  queue_enabled?: boolean;
  queue_depth?: number;
  queued_tasks?: number;
  running_tasks?: number;
  active_workers?: number;
  worker_concurrency?: number;
  owner_concurrency?: number;
  owner_pending_limit?: number;
};

export type MonitoringLatencySummary = {
  sample_size: number;
  average_ms: number;
  p95_ms: number;
  max_ms: number;
};

export type MonitoringModelStat = {
  model: string;
  cost_count: number;
  cost_total: number;
  cost_average: number;
};

export type MonitoringStageLatencySummary = {
  upload: MonitoringLatencySummary;
  queue: MonitoringLatencySummary;
  generation: MonitoringLatencySummary;
  save: MonitoringLatencySummary;
};

export type MonitoringSummary = {
  source?: MonitoringSource;
  online_users: number;
  active_sessions: number;
  total_success: number;
  total_failed: number;
  total_cost?: number;
  cost_count?: number;
  models?: MonitoringModelStat[];
  total_users: number;
  online_window_minutes: number;
  range: {
    start_at: string;
    end_at: string;
    end_exclusive: boolean;
  };
  task_queue: MonitoringQueueSummary;
  agent_queue?: MonitoringAgentQueueSummary;
  video_generation_queue?: MonitoringVideoGenerationQueueSummary;
  audio_generation_queue?: MonitoringVideoGenerationQueueSummary;
  task_latency: MonitoringLatencySummary;
  stage_latency: MonitoringStageLatencySummary;
  users: MonitoringUserStat[];
};

export type MonitoringTaskDetail = {
  row_key: string;
  source_type?: "image" | "event" | "video" | "audio" | string;
  task_id: string;
  owner_id: string;
  status: "queued" | "running" | "success" | "error" | "canceled";
  image_count: number;
  media_count?: number;
  mode: string;
  model: string;
  duration_ms: number;
  cost?: number | null;
  upstream_task_id?: string;
  error: string;
  completed_at: string;
  image_url: string;
  video_url?: string;
  audio_url?: string;
  cover_url?: string;
  voice_id?: string;
  output_format?: string;
  reference_images?: MonitoringTaskReferenceImage[];
};

export type MonitoringTaskReferenceImage = {
  preview_url: string;
  filename: string;
  mime_type: string;
  role?: string;
  kind?: string;
  rel?: string;
};

export type MonitoringTaskDetails = {
  source?: MonitoringSource;
  items: MonitoringTaskDetail[];
  record_count: number;
  image_count: number;
  media_count?: number;
  cost_total?: number;
  cost_count?: number;
  limit: number;
  offset?: number;
  truncated: boolean;
  has_more?: boolean;
  next_cursor?: { event_at: string; task_key: string } | null;
  range: MonitoringSummary["range"];
  owner_id: string;
  status: "all" | "queued" | "running" | "success" | "error" | "canceled";
};

export type UpstreamBillingSource = "all" | "image" | "video" | "chat" | "audio";

export type UpstreamBillingSyncStatus = {
  provider: string;
  enabled: boolean;
  configured: boolean;
  scope: "key" | "user";
  status: "idle" | "running" | "success" | "error" | "unavailable" | string;
  last_started_at: string;
  last_success_at: string;
  last_error_at: string;
  last_error: string;
  last_window_from: string;
  last_window_to: string;
  last_full_sync_at: string;
  records_seen: number;
  records_upserted: number;
  interval_secs: number;
  lookback_days: number;
  full_lookback_days: number;
};

export type UpstreamBillingModelStat = {
  model: string;
  model_version?: string;
  model_type: string;
  count: number;
  success_count: number;
  failed_count: number;
  cost: number;
  refunded_count: number;
  refunded_amount: number;
};

export type UpstreamBillingUserStat = {
  owner_id: string;
  username: string;
  name: string;
  count: number;
  cost: number;
  refunded_count: number;
  refunded_amount: number;
};

export type UpstreamBillingSummary = {
  source: UpstreamBillingSource;
  unit: string;
  record_count: number;
  success_count: number;
  failed_count: number;
  total_cost: number;
  refunded_count: number;
  refunded_amount: number;
  assigned_count: number;
  unassigned_count: number;
  models: UpstreamBillingModelStat[];
  users: UpstreamBillingUserStat[];
  range: MonitoringSummary["range"];
  sync: UpstreamBillingSyncStatus;
};

export type UpstreamBillingRecord = {
  row_key: string;
  upstream_task_id: string;
  model: string;
  upstream_model: string;
  requested_model: string;
  model_version: string;
  model_type: string;
  channel_group: string;
  state: string;
  unit: string;
  cost: number;
  refunded: boolean;
  refunded_amount: number;
  created_at: string;
  completed_at: string;
  event_at: string;
  local_source: string;
  local_task_id: string;
  owner_id: string;
  owner_username: string;
  owner_name: string;
  attribution_status: "matched" | "unassigned";
};

export type UpstreamBillingRecords = {
  source: UpstreamBillingSource;
  items: UpstreamBillingRecord[];
  record_count: number;
  cost_total: number;
  refunded_amount: number;
  unit: string;
  limit: number;
  offset: number;
  has_more: boolean;
  query: string;
  range: MonitoringSummary["range"];
};

export type AgentVideoAsset = {
  videoId: string;
  conversationId?: string;
  name: string;
  filename?: string;
  type: string;
  mimeType?: string;
  size: number;
  fileSize?: number;
  url: string;
  sha256?: string;
  cached?: boolean;
  status?: string;
  analysisStatus?: string;
  analysisError?: string;
  analysis?: Record<string, unknown>;
  analysisStartedAt?: string;
  analysisFinishedAt?: string;
  analysisVersion?: number;
  createdAt?: string;
  updatedAt?: string;
};

export type AgentVideoUploadResponse = {
  items: AgentVideoAsset[];
  total: number;
  uploaded: number;
  cache_hits: number;
  analysisQueueErrors?: Array<{ videoId: string; error: string }>;
};

export type AgentVideoStatusResponse = {
  items: AgentVideoAsset[];
  missing: string[];
};

export type ReferenceUploadItem = {
  url: string;
  sha256: string;
  filename: string;
  mime_type: string;
  file_size: number;
  cached: boolean;
  upload_ms: number;
};

export type ReferenceUploadResponse = {
  items: ReferenceUploadItem[];
  total: number;
  uploaded: number;
  cache_hits: number;
  duration_ms: number;
};

export async function login(username: string, password: string) {
  return httpRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: { username, password },
    redirectOnUnauthorized: false,
  });
}

export function resolveApiAssetUrl(value?: string | null) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (/^(https?:|data:|blob:)/i.test(raw)) return raw;
  if (!raw.startsWith("/")) return raw;
  return `${webConfig.apiUrl}${raw}`;
}

export async function fetchCaptcha() {
  return httpRequest<CaptchaResponse>(`/auth/captcha?_t=${Date.now()}`, {
    redirectOnUnauthorized: false,
  });
}

export async function register(body: {
  username: string;
  password: string;
  name?: string;
  captcha_id: string;
  captcha_code: string;
}) {
  return httpRequest<LoginResponse>("/auth/register", {
    method: "POST",
    body,
    redirectOnUnauthorized: false,
  });
}

export async function fetchCurrentUser() {
  return httpRequest<CurrentUserResponse>("/api/auth/me", {
    redirectOnUnauthorized: false,
  });
}

export async function uploadAvatar(file: File) {
  const formData = new FormData();
  formData.append("avatar", file);
  return httpRequest<CurrentUserResponse>("/api/auth/avatar", {
    method: "POST",
    body: formData,
  });
}

export async function updateCurrentUserProfile(body: { username?: string; name?: string }) {
  return httpRequest<CurrentUserResponse>("/api/auth/profile", {
    method: "PATCH",
    body,
  });
}

export async function logout() {
  return httpRequest<{ ok: boolean }>("/api/auth/logout", {
    method: "POST",
    redirectOnUnauthorized: false,
  });
}

export async function changePassword(body: { currentPassword: string; newPassword: string }) {
  return httpRequest<{ ok: boolean }>("/api/auth/change-password", {
    method: "POST",
    body: {
      current_password: body.currentPassword,
      new_password: body.newPassword,
    },
    redirectOnUnauthorized: false,
  });
}

export async function fetchUsers() {
  return httpRequest<{ items: UserAccount[]; total: number }>(`/api/users?_t=${Date.now()}`);
}

export async function fetchMonitoringSummary(options: { startAt?: string; endAt?: string; source?: MonitoringSource } = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.startAt) params.set("startAt", options.startAt);
  if (options.endAt) params.set("endAt", options.endAt);
  if (options.source) params.set("source", options.source);
  return httpRequest<MonitoringSummary>(`/api/monitoring/summary?${params.toString()}`);
}

export async function fetchMonitoringQueues() {
  return httpRequest<MonitoringQueuesResponse>(`/api/monitoring/queues?_t=${Date.now()}`);
}

export async function fetchMonitoringTasks(options: {
  startAt?: string;
  endAt?: string;
  ownerId?: string;
  status?: "all" | "queued" | "running" | "success" | "error" | "canceled";
  source?: MonitoringSource;
  limit?: number;
  offset?: number;
  q?: string;
  costOnly?: boolean;
  includeReferences?: boolean;
  cursor?: { event_at: string; task_key: string } | null;
} = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.startAt) params.set("startAt", options.startAt);
  if (options.endAt) params.set("endAt", options.endAt);
  if (options.ownerId) params.set("ownerId", options.ownerId);
  if (options.status) params.set("status", options.status);
  if (options.source) params.set("source", options.source);
  if (options.limit) params.set("limit", String(options.limit));
  if (options.offset) params.set("offset", String(options.offset));
  if (options.q?.trim()) params.set("q", options.q.trim());
  if (options.costOnly) params.set("costOnly", "1");
  if (options.includeReferences) params.set("includeReferences", "1");
  if (options.cursor?.event_at && options.cursor.task_key) {
    params.set("cursorAt", options.cursor.event_at);
    params.set("cursorKey", options.cursor.task_key);
  }
  return httpRequest<MonitoringTaskDetails>(`/api/monitoring/tasks?${params.toString()}`);
}

export async function fetchUpstreamBillingSummary(options: {
  source?: UpstreamBillingSource;
  startAt?: string;
  endAt?: string;
} = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.source) params.set("source", options.source);
  if (options.startAt) params.set("startAt", options.startAt);
  if (options.endAt) params.set("endAt", options.endAt);
  return httpRequest<UpstreamBillingSummary>(`/api/billing/summary?${params.toString()}`);
}

export async function fetchUpstreamBillingRecords(options: {
  source?: UpstreamBillingSource;
  startAt?: string;
  endAt?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.source) params.set("source", options.source);
  if (options.startAt) params.set("startAt", options.startAt);
  if (options.endAt) params.set("endAt", options.endAt);
  if (options.q?.trim()) params.set("q", options.q.trim());
  if (options.limit) params.set("limit", String(options.limit));
  if (options.offset) params.set("offset", String(options.offset));
  return httpRequest<UpstreamBillingRecords>(`/api/billing/records?${params.toString()}`);
}

export async function fetchUpstreamBillingSyncStatus() {
  return httpRequest<UpstreamBillingSyncStatus>(`/api/billing/sync-status?_t=${Date.now()}`);
}

export async function syncUpstreamBilling(days = 2) {
  const params = new URLSearchParams({ days: String(Math.min(30, Math.max(1, days))) });
  return httpRequest<{
    ok: boolean;
    provider: string;
    scope: string;
    days: number;
    records_seen: number;
    records_upserted: number;
    window_from: string;
    window_to: string;
    sync: UpstreamBillingSyncStatus;
  }>(`/api/billing/sync?${params.toString()}`, { method: "POST" });
}

export async function fetchVideoGenerationQueue() {
  return httpRequest<MonitoringVideoGenerationQueueSummary>(`/api/video-generation/queue?_t=${Date.now()}`);
}

export async function createUser(body: {
  username: string;
  password: string;
  name?: string;
  role?: AuthRole;
  enabled?: boolean;
}) {
  return httpRequest<UserAccount>("/api/users", {
    method: "POST",
    body,
  });
}

export async function updateUser(id: string, body: Partial<Pick<UserAccount, "name" | "role" | "enabled">> & { password?: string }) {
  return httpRequest<UserAccount>(`/api/users/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export async function disableUser(id: string) {
  return httpRequest<UserAccount>(`/api/users/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function fetchModels() {
  return httpRequest<ModelListResponse>("/v1/models");
}

export async function analyzeImagePrompt(body: {
  action: ImagePromptAnalysisAction;
  mode: "single";
  prompt: string;
  images: Array<{ name: string; dataUrl: string }>;
  product?: {
    name?: string;
    sku?: string;
    brand?: string;
    category?: string;
    sellingPoints?: string;
  };
}) {
  return httpRequest<ImagePromptAnalysisResponse>("/api/image-prompt/analyze", {
    method: "POST",
    body,
  });
}

export async function startImageAgentRun(body: {
  prompt: string;
  agentEngine?: ProfessionalAgentEngine;
  mode: "generate" | "edit";
  model: ImageModel;
  size?: string;
  quality: string;
  count: number;
  sceneType: ProfessionalSceneType;
  preserveSubject?: boolean;
  inheritReferenceImages?: boolean;
  useLongTermMemory?: boolean;
  conversationId: string;
  turnId: string;
  folderId?: string;
  images?: Array<{ name: string; type: string; dataUrl?: string; url?: string; role?: "working_canvas" | "product_anchor" | "reference" | string }>;
  videos?: AgentVideoAsset[];
  conversationContext?: Array<{
    userRequest: string;
    sceneName?: string;
    proposalSummary?: string;
    visualDirection?: string;
    resultStatus?: string;
    assistantMessage?: string;
    suggestions?: string[];
    creativeBrief?: ImageAgentCreativeBrief;
  }>;
}) {
  return httpRequest<{ agentRun: AgentRun }>("/api/image-agent/runs", {
    method: "POST",
    body,
    timeout: 120_000,
  });
}

export async function uploadAgentVideos(files: File[], conversationId = "", analyze = false) {
  const formData = new FormData();
  files.forEach((file) => formData.append("videos", file, file.name));
  if (conversationId) formData.append("conversation_id", conversationId);
  if (analyze) formData.append("analyze", "true");
  return httpRequest<AgentVideoUploadResponse>("/api/image-agent/videos", {
    method: "POST",
    body: formData,
    timeout: 600_000,
  });
}

export async function fetchAgentVideoStatuses(ids: string[]) {
  const uniqueIds = Array.from(new Set(ids.map((id) => String(id || "").trim()).filter(Boolean))).slice(0, 50);
  if (!uniqueIds.length) return { items: [], missing: [] } as AgentVideoStatusResponse;
  return httpRequest<AgentVideoStatusResponse>("/api/image-agent/videos/status", {
    method: "POST",
    body: { ids: uniqueIds },
  });
}

export async function retryAgentVideoAnalysis(videoId: string) {
  return httpRequest<{ item: AgentVideoAsset }>(`/api/image-agent/videos/${encodeURIComponent(videoId)}/retry-analysis`, {
    method: "POST",
  });
}

export async function fetchAgentMemories(options: {
  conversationId?: string;
  projectId?: string;
  brandId?: string;
  includePending?: boolean;
} = {}) {
  const params = new URLSearchParams();
  if (options.conversationId) params.set("conversationId", options.conversationId);
  if (options.projectId) params.set("projectId", options.projectId);
  if (options.brandId) params.set("brandId", options.brandId);
  if (options.includePending) params.set("includePending", "true");
  params.set("limit", "200");
  return httpRequest<{ items: AgentMemoryItem[]; counts?: AgentMemoryCounts }>(`/api/image-agent/memory?${params.toString()}`);
}

export async function createAgentMemory(body: {
  content: string;
  category: string;
  scope: AgentMemoryScope;
  scopeId?: string;
  conversationId?: string;
}) {
  return httpRequest<{ item: AgentMemoryItem }>("/api/image-agent/memory", { method: "POST", body });
}

export async function updateAgentMemory(memoryId: string, body: {
  content: string;
  category: string;
  scope: AgentMemoryScope;
  scopeId?: string;
}) {
  return httpRequest<{ item: AgentMemoryItem }>(`/api/image-agent/memory/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    body,
  });
}

export async function deleteAgentMemory(memoryId: string) {
  return httpRequest<{ ok: boolean }>(`/api/image-agent/memory/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
}

export async function reviewAgentMemory(memoryId: string, decision: "approve" | "reject") {
  return httpRequest<{ item: AgentMemoryItem }>(`/api/image-agent/memory/${encodeURIComponent(memoryId)}/review`, {
    method: "POST",
    body: { decision },
  });
}

export async function clearAgentMemories() {
  return httpRequest<{ ok: boolean; deleted: number }>("/api/image-agent/memory", { method: "DELETE" });
}

export async function uploadAgentFolder(files: File[], folderName = "上传文件夹", conversationId = "") {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append("files", file, file.name);
    formData.append("relative_names", file.webkitRelativePath || file.name);
  });
  formData.append("folder_name", folderName);
  if (conversationId) formData.append("conversation_id", conversationId);
  return httpRequest<AgentFolderAsset>("/api/image-agent/folders", {
    method: "POST",
    body: formData,
    timeout: 600_000,
  });
}

export async function fetchAgentFolder(folderId: string) {
  return httpRequest<AgentFolderAsset>(`/api/image-agent/folders/${encodeURIComponent(folderId)}`);
}

export async function deleteAgentFolder(folderId: string) {
  return httpRequest<{ ok: boolean }>(`/api/image-agent/folders/${encodeURIComponent(folderId)}`, { method: "DELETE" });
}

export async function fetchAgentBatchPlan(planId: string) {
  return httpRequest<AgentBatchPlan>(`/api/image-agent/batch-plans/${encodeURIComponent(planId)}`);
}

export async function retryAgentBatchItem(planId: string, itemId: number, body: { model?: ImageModel; size?: string; quality?: string } = {}) {
  return httpRequest<AgentBatchPlan>(`/api/image-agent/batch-plans/${encodeURIComponent(planId)}/items/${encodeURIComponent(String(itemId))}/retry`, {
    method: "POST",
    body: {
      model: body.model || "gpt-image-2",
      size: body.size || "",
      quality: body.quality || "auto",
    },
  });
}

export async function fetchAgentRun(runId: string, includeResult = false) {
  return httpRequest<{ agentRun: AgentRun }>(`/api/agent/runs/${encodeURIComponent(runId)}${includeResult ? "?includeResult=true" : ""}`);
}

export async function cancelAgentRun(runId: string) {
  return httpRequest<{ agentRun: AgentRun }>(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export async function streamAgentRunEvents(
  runId: string,
  onEvent: (event: AgentEvent) => void | Promise<void>,
  options: { after?: number; signal?: AbortSignal } = {},
) {
  const authKey = await getStoredAuthKey();
  const after = Math.max(0, Math.floor(options.after || 0));
  const response = await fetch(`${webConfig.apiUrl}/api/agent/runs/${encodeURIComponent(runId)}/events?after=${after}`, {
    method: "GET",
    headers: {
      Accept: "text/event-stream",
      ...(authKey ? { Authorization: `Bearer ${authKey}` } : {}),
    },
    signal: options.signal,
  });
  if (!response.ok) {
    let message = `Agent 事件流连接失败 (${response.status})`;
    try {
      const payload = await response.json() as { detail?: { error?: string } | string };
      message = typeof payload.detail === "string" ? payload.detail : payload.detail?.error || message;
    } catch {
      // Keep the HTTP status fallback when the server did not return JSON.
    }
    throw new Error(message);
  }
  if (!response.body) throw new Error("当前浏览器无法读取 Agent 事件流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      await onEvent(JSON.parse(data) as AgentEvent);
    }
    if (done) break;
  }
}

export async function streamRealtimeEvents(
  onEvent: (event: RealtimeEvent) => void | Promise<void>,
  options: { signal?: AbortSignal } = {},
) {
  const authKey = await getStoredAuthKey();
  const response = await fetch(`${webConfig.apiUrl}/api/events/stream`, {
    method: "GET",
    headers: {
      Accept: "text/event-stream",
      ...(authKey ? { Authorization: `Bearer ${authKey}` } : {}),
    },
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`实时事件连接失败 (${response.status})`);
  if (!response.body) throw new Error("当前浏览器无法读取实时事件流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      let eventType = "message";
      const dataLines: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length || (eventType !== "announcement" && eventType !== "image_task_progress")) continue;
      await onEvent({ type: eventType, data: JSON.parse(dataLines.join("\n")) } as RealtimeEvent);
    }
    if (done) break;
  }
}

export async function createImageGenerationTask(
  clientTaskId: string,
  prompt: string,
  model?: ImageModel,
  size?: string,
  quality = "auto",
  conversationId?: string,
  turnId?: string,
  productId?: number,
  templateId?: number,
  batchId?: string,
  batchIndex = 0,
  batchTotal = 1,
  promptEngineMode: PromptEngineMode = "standard",
  imageOptions: ImageTaskImageOptions = {},
) {
  return httpRequest<ImageTask>("/api/image-tasks/generations", {
    method: "POST",
    body: {
      client_task_id: clientTaskId,
      prompt,
      ...(model ? { model } : {}),
      ...(size ? { size } : {}),
      quality,
      ...(imageOptions.aspectRatio ? { aspect_ratio: imageOptions.aspectRatio } : {}),
      ...(imageOptions.imageSize ? { image_size: imageOptions.imageSize } : {}),
      ...(imageOptions.thinkingLevel ? { thinking_level: imageOptions.thinkingLevel } : {}),
      ...(imageOptions.imageCount ? { n: Math.min(4, Math.max(1, Math.round(imageOptions.imageCount))) } : {}),
      ...(imageOptions.source ? { source: imageOptions.source } : {}),
      ...(imageOptions.source === "canvas" ? { canvas_units: Math.max(1, Math.round(imageOptions.canvasUnits || imageOptions.imageCount || 1)) } : {}),
      prompt_engine_mode: promptEngineMode,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(turnId ? { turn_id: turnId } : {}),
      ...(productId ? { product_id: productId } : {}),
      ...(templateId ? { template_id: templateId } : {}),
      ...(batchId ? { batch_id: batchId, batch_index: batchIndex, batch_total: batchTotal } : {}),
    },
  });
}

export async function createImageEditTask(
  clientTaskId: string,
  files: File | File[],
  prompt: string,
  model?: ImageModel,
  size?: string,
  quality = "auto",
  imageUrls: string[] = [],
  preserveSubject = false,
  conversationId?: string,
  turnId?: string,
  productId?: number,
  templateId?: number,
  batchId?: string,
  batchIndex = 0,
  batchTotal = 1,
  referenceUploadMs = 0,
  referenceCacheHits = 0,
  promptEngineMode: PromptEngineMode = "standard",
  imageOptions: ImageTaskImageOptions = {},
) {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];

  uploadFiles.forEach((file) => {
    formData.append("image", file);
  });
  imageUrls.forEach((url) => {
    formData.append("image_url", url);
  });
  formData.append("client_task_id", clientTaskId);
  formData.append("prompt", prompt);
  if (model) {
    formData.append("model", model);
  }
  if (size) {
    formData.append("size", size);
  }
  formData.append("quality", quality);
  if (imageOptions.aspectRatio) {
    formData.append("aspect_ratio", imageOptions.aspectRatio);
  }
  if (imageOptions.imageSize) {
    formData.append("image_size", imageOptions.imageSize);
  }
  if (imageOptions.thinkingLevel) {
    formData.append("thinking_level", imageOptions.thinkingLevel);
  }
  if (imageOptions.imageCount) {
    formData.append("n", String(Math.min(4, Math.max(1, Math.round(imageOptions.imageCount)))));
  }
  if (imageOptions.source) {
    formData.append("source", imageOptions.source);
  }
  if (imageOptions.source === "canvas") {
    formData.append("canvas_units", String(Math.max(1, Math.round(imageOptions.canvasUnits || imageOptions.imageCount || 1))));
  }
  formData.append("prompt_engine_mode", promptEngineMode);
  formData.append("preserve_subject", preserveSubject ? "true" : "false");
  if (conversationId) {
    formData.append("conversation_id", conversationId);
  }
  if (turnId) {
    formData.append("turn_id", turnId);
  }
  if (productId) {
    formData.append("product_id", String(productId));
  }
  if (templateId) {
    formData.append("template_id", String(templateId));
  }
  if (batchId) {
    formData.append("batch_id", batchId);
    formData.append("batch_index", String(batchIndex));
    formData.append("batch_total", String(batchTotal));
  }
  formData.append("reference_upload_ms", String(Math.max(0, Math.round(referenceUploadMs))));
  formData.append("reference_cache_hits", String(Math.max(0, Math.round(referenceCacheHits))));

  return httpRequest<ImageTask>("/api/image-tasks/edits", {
    method: "POST",
    body: formData,
  });
}

export async function preuploadImageReferences(files: File[]) {
  const formData = new FormData();
  files.forEach((file) => formData.append("images", file));
  return httpRequest<ReferenceUploadResponse>("/api/image-references/preupload", {
    method: "POST",
    body: formData,
  });
}

export async function fetchImageTasks(ids: string[]) {
  const uniqueIds = Array.from(new Set(ids.map((id) => id.trim()).filter(Boolean)));
  if (!uniqueIds.length) {
    return httpRequest<ImageTaskListResponse>(`/api/image-tasks?_t=${Date.now()}`);
  }

  const chunkSize = 100;
  const chunks = Array.from(
    { length: Math.ceil(uniqueIds.length / chunkSize) },
    (_, index) => uniqueIds.slice(index * chunkSize, (index + 1) * chunkSize),
  );
  const responses = await Promise.all(
    chunks.map((chunk) =>
      httpRequest<ImageTaskListResponse>("/api/image-tasks/query", {
        method: "POST",
        body: { ids: chunk },
      }),
    ),
  );
  return {
    items: responses.flatMap((response) => response.items),
    missing_ids: responses.flatMap((response) => response.missing_ids),
  };
}

export async function fetchImageConversationsRemote(options: {
  limit?: number;
  cursor?: ImageConversationCursor | null;
} = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor?.updated_at && options.cursor.id) {
    params.set("cursorAt", options.cursor.updated_at);
    params.set("cursorId", options.cursor.id);
  }
  return httpRequest<ImageConversationListResponse>(`/api/image-conversations?${params.toString()}`);
}

export async function fetchImageConversationCount() {
  return httpRequest<{ count: number }>(`/api/image-conversations/count?_t=${Date.now()}`);
}

export async function upsertImageConversationRemote(conversation: ImageConversationApiPayload, headers?: Record<string, string>) {
  const id = String(conversation.id || "").trim();
  return httpRequest<ImageConversationApiPayload>(`/api/image-conversations/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { conversation },
    headers,
  });
}

export async function renameImageConversationRemote(id: string, title: string, headers?: Record<string, string>) {
  return httpRequest<ImageConversationApiPayload>(`/api/image-conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: { title },
    headers,
  });
}

export async function deleteImageConversationRemote(id: string, headers?: Record<string, string>) {
  return httpRequest<{ ok: boolean }>(`/api/image-conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers,
  });
}

export async function clearImageConversationsRemote(headers?: Record<string, string>) {
  return httpRequest<{ ok: boolean; deleted: number }>("/api/image-conversations", {
    method: "DELETE",
    headers,
  });
}

function downloadErrorMessageFromValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const item = value as { detail?: unknown; error?: unknown; message?: unknown };
  return (
    downloadErrorMessageFromValue(item.detail) ||
    downloadErrorMessageFromValue(item.error) ||
    (typeof item.message === "string" ? item.message : "")
  );
}

async function downloadErrorMessage(error: unknown) {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    const text = await data.text();
    if (!text) return "";
    try {
      return downloadErrorMessageFromValue(JSON.parse(text)) || text;
    } catch {
      return text;
    }
  }
  return error instanceof Error ? error.message : "";
}

function filenameFromContentDisposition(value: unknown) {
  const header = String(value || "");
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return encoded;
    }
  }
  return header.match(/filename="([^"]+)"/i)?.[1] || header.match(/filename=([^;]+)/i)?.[1]?.trim() || "";
}

export async function downloadImageTaskZip(body: {
  folderName: string;
  items: Array<{ taskId: string; imageIndex?: number; filename: string }>;
}) {
  try {
    const response = await request.request<Blob>({
      url: "/api/image-tasks/download-zip",
      method: "POST",
      responseType: "blob",
      data: {
        folder_name: body.folderName,
        items: body.items.map((item) => ({
          task_id: item.taskId,
          image_index: item.imageIndex ?? 0,
          filename: item.filename,
        })),
      },
    });
    return response.data;
  } catch (error) {
    throw new Error((await downloadErrorMessage(error)) || "打包下载失败");
  }
}

export async function resumeImagePoll(taskId: string, extraTimeoutSecs = 30) {
  return httpRequest<ImageTask>(`/api/image-tasks/${encodeURIComponent(taskId)}/resume-poll`, {
    method: "POST",
    body: { extra_timeout_secs: extraTimeoutSecs },
  });
}

export async function cancelImageTask(taskId: string) {
  return httpRequest<ImageTask>(`/api/image-tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
}

export async function createVideoGenerationTask(body: {
  clientTaskId: string;
  prompt: string;
  model: string;
  mode?: "text_to_video" | "image_to_video";
  aspectRatio?: string;
  durationSecs?: VideoGenerationDuration;
  quality?: string;
  resolution?: string;
  imageUrls?: string[];
  params?: Record<string, unknown>;
  conversationId?: string;
  turnId?: string;
  source?: "standard" | "canvas";
  canvasUnits?: number;
}) {
  return httpRequest<VideoGenerationTask>("/api/video-generation/tasks", {
    method: "POST",
    timeout: 30_000,
    body: {
      client_task_id: body.clientTaskId,
      prompt: body.prompt,
      model: body.model,
      mode: body.mode || (body.imageUrls?.length ? "image_to_video" : "text_to_video"),
      aspect_ratio: body.aspectRatio || "16:9",
      duration_secs: body.durationSecs ?? 5,
      quality: body.quality || "standard",
      resolution: body.resolution || "",
      image_urls: body.imageUrls || [],
      params: body.params || {},
      conversation_id: body.conversationId || "",
      turn_id: body.turnId || "",
      source: body.source || "standard",
      canvas_units: body.source === "canvas" ? Math.max(1, Math.round(body.canvasUnits || 1)) : 1,
    },
  });
}

export async function fetchVideoGenerationTasks(
  ids: string[] = [],
  options: { limit?: number; cursor?: string; status?: VideoGenerationTask["status"]; q?: string; allOwners?: boolean; ownerId?: string; conversationId?: string } = {},
) {
  const uniqueIds = Array.from(new Set(ids.map((id) => id.trim()).filter(Boolean)));
  if (!uniqueIds.length) {
    const params = new URLSearchParams({ _t: String(Date.now()) });
    if (options.limit) params.set("limit", String(options.limit));
    if (options.cursor) params.set("cursor", options.cursor);
    if (options.status) params.set("status", options.status);
    if (options.q?.trim()) params.set("q", options.q.trim());
    if (options.allOwners) params.set("all_owners", "true");
    if (options.ownerId?.trim()) params.set("owner_id", options.ownerId.trim());
    if (options.conversationId?.trim()) params.set("conversation_id", options.conversationId.trim());
    return httpRequest<VideoGenerationTaskListResponse>(`/api/video-generation/tasks?${params.toString()}`, { timeout: 30_000 });
  }
  const chunkSize = 100;
  const chunks = Array.from(
    { length: Math.ceil(uniqueIds.length / chunkSize) },
    (_, index) => uniqueIds.slice(index * chunkSize, (index + 1) * chunkSize),
  );
  const responses = await Promise.all(
    chunks.map((chunk) =>
      httpRequest<VideoGenerationTaskListResponse>("/api/video-generation/tasks/query", {
        method: "POST",
        body: { ids: chunk },
        timeout: 30_000,
      }),
    ),
  );
  return {
    items: responses.flatMap((response) => response.items),
    missing_ids: responses.flatMap((response) => response.missing_ids || []),
  } as VideoGenerationTaskListResponse;
}

export async function cancelVideoGenerationTask(taskId: string) {
  return httpRequest<VideoGenerationTask>(`/api/video-generation/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
}

export async function reconcileVideoGenerationTask(taskId: string) {
  return httpRequest<VideoGenerationTask>(`/api/video-generation/tasks/${encodeURIComponent(taskId)}/reconcile`, {
    method: "POST",
  });
}

export async function deleteVideoGenerationTask(taskId: string) {
  return httpRequest<{ ok: boolean; deleted: number }>(`/api/video-generation/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export async function deleteVideoGenerationConversation(conversationId: string) {
  return httpRequest<{ ok: boolean; deleted: number }>(`/api/video-generation/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
  });
}

export async function createVideoAgentPlan(body: {
  prompt: string;
  conversationId?: string;
  turnId?: string;
  images?: ReferenceUploadItem[];
  videos?: AgentVideoAsset[];
}) {
  return httpRequest<VideoAgentPlan>("/api/video-agent/plans", {
    method: "POST",
    body: {
      prompt: body.prompt,
      conversation_id: body.conversationId || "",
      turn_id: body.turnId || "",
      images: (body.images || []).map((item) => ({
        name: item.filename || "image",
        filename: item.filename || "",
        type: item.mime_type || "image/jpeg",
        mime_type: item.mime_type || "image/jpeg",
        url: item.url,
        sha256: item.sha256 || "",
        size: item.file_size || 0,
      })),
      videos: (body.videos || []).map((item) => ({
        video_id: item.videoId,
        name: item.name || item.filename || "video.mp4",
        filename: item.filename || item.name || "video.mp4",
        type: item.type || item.mimeType || "video/mp4",
        mime_type: item.mimeType || item.type || "video/mp4",
        url: item.url,
        sha256: item.sha256 || "",
        size: item.size || item.fileSize || 0,
      })),
    },
  });
}

export async function streamVideoAgentPlan(
  body: {
    prompt: string;
    conversationId?: string;
    turnId?: string;
    images?: ReferenceUploadItem[];
    videos?: AgentVideoAsset[];
  },
  onEvent: (event: VideoAgentPlanStreamEvent) => void | Promise<void>,
  options: { signal?: AbortSignal } = {},
) {
  const authKey = await getStoredAuthKey();
  const response = await fetch(`${webConfig.apiUrl}/api/video-agent/plans/stream`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      ...(authKey ? { Authorization: `Bearer ${authKey}` } : {}),
    },
    body: JSON.stringify({
      prompt: body.prompt,
      conversation_id: body.conversationId || "",
      turn_id: body.turnId || "",
      reasoning: true,
      images: (body.images || []).map((item) => ({
        name: item.filename || "image",
        filename: item.filename || "",
        type: item.mime_type || "image/jpeg",
        mime_type: item.mime_type || "image/jpeg",
        url: item.url,
        sha256: item.sha256 || "",
        size: item.file_size || 0,
      })),
      videos: (body.videos || []).map((item) => ({
        video_id: item.videoId,
        name: item.name || item.filename || "video.mp4",
        filename: item.filename || item.name || "video.mp4",
        type: item.type || item.mimeType || "video/mp4",
        mime_type: item.mimeType || item.type || "video/mp4",
        url: item.url,
        sha256: item.sha256 || "",
        size: item.size || item.fileSize || 0,
      })),
    }),
    signal: options.signal,
  });
  if (!response.ok) {
    let message = `推理请求失败 (${response.status})`;
    try {
      const payload = await response.json() as {
        detail?: string | { error?: string | { message?: string } };
      };
      const detail = payload.detail;
      if (typeof detail === "string") message = detail;
      else if (typeof detail?.error === "string") message = detail.error;
      else if (detail?.error?.message) message = detail.error.message;
    } catch {
      // Keep the HTTP status fallback when the server did not return JSON.
    }
    throw new Error(message);
  }
  if (!response.body) throw new Error("当前浏览器无法读取推理事件流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      const event = JSON.parse(data) as VideoAgentPlanStreamEvent;
      if (event.type === "error") throw new Error(event.message || "推理请求失败");
      await onEvent(event);
    }
    if (done) break;
  }
}

export async function fetchVideoAgentPlans(options: { limit?: number; conversationId?: string } = {}) {
  const params = new URLSearchParams({
    limit: String(options.limit || 200),
    _t: String(Date.now()),
  });
  if (options.conversationId?.trim()) params.set("conversation_id", options.conversationId.trim());
  return httpRequest<VideoAgentPlanListResponse>(`/api/video-agent/plans?${params.toString()}`);
}

export async function deleteVideoAgentPlan(planId: string) {
  return httpRequest<{ ok: boolean; deleted: number }>(`/api/video-agent/plans/${encodeURIComponent(planId)}`, {
    method: "DELETE",
  });
}

export async function deleteVideoAgentConversation(conversationId: string) {
  return httpRequest<{ ok: boolean; deleted: number }>(`/api/video-agent/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
  });
}

export async function reportImageFailure(body: {
  taskId: string;
  failureReportId?: string;
  error?: string;
  imageCount?: number;
  mode?: "generate" | "edit";
  model?: ImageModel;
  productId?: number;
  templateId?: number;
}) {
  return httpRequest<{ ok: boolean }>("/api/image-tasks/failure-reports", {
    method: "POST",
    body: {
      task_id: body.taskId,
      failure_report_id: body.failureReportId || body.taskId,
      error: body.error || "",
      image_count: body.imageCount || 1,
      mode: body.mode || "generate",
      model: body.model || "",
      product_id: body.productId || 0,
      template_id: body.templateId || 0,
    },
  });
}

export async function fetchSettingsConfig() {
  return httpRequest<{ config: SettingsConfig }>("/api/settings");
}

export async function fetchSystemAnnouncements(options: { includeDisabled?: boolean; limit?: number } = {}) {
  const params = new URLSearchParams({
    limit: String(options.limit || 5),
    _t: String(Date.now()),
  });
  if (options.includeDisabled) params.set("include_disabled", "true");
  return httpRequest<{ items: SystemAnnouncement[]; total: number }>(`/api/system/announcements?${params.toString()}`);
}

export async function createSystemAnnouncement(body: Pick<SystemAnnouncement, "title" | "content"> & { type?: string; enabled?: boolean }) {
  return httpRequest<SystemAnnouncement>("/api/system/announcements", {
    method: "POST",
    body,
  });
}

export async function updateSystemAnnouncement(id: number, body: Partial<Pick<SystemAnnouncement, "title" | "content" | "type" | "enabled">>) {
  return httpRequest<SystemAnnouncement>(`/api/system/announcements/${id}`, {
    method: "PATCH",
    body,
  });
}

export async function disableSystemAnnouncement(id: number) {
  return httpRequest<SystemAnnouncement>(`/api/system/announcements/${id}`, {
    method: "DELETE",
  });
}

export async function fetchImageLibrary(options: {
  limit?: number;
  offset?: number;
  cursor?: ImageLibraryCursor | null;
  q?: string;
  productId?: number;
  templateId?: number;
  favorite?: boolean;
  allOwners?: boolean;
  ownerId?: string;
} = {}) {
  const {
    limit = 80,
    offset = 0,
    cursor = null,
    q = "",
    productId = 0,
    templateId = 0,
    favorite = false,
    allOwners = false,
    ownerId = "",
  } = options;
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    _t: String(Date.now()),
  });
  if (cursor?.created_at && cursor.id) {
    params.set("cursor_created_at", cursor.created_at);
    params.set("cursor_id", String(cursor.id));
  }
  if (q.trim()) {
    params.set("q", q.trim());
  }
  if (productId) {
    params.set("product_id", String(productId));
  }
  if (templateId) {
    params.set("template_id", String(templateId));
  }
  if (favorite) {
    params.set("favorite", "true");
  }
  if (allOwners) {
    params.set("all_owners", "true");
  }
  if (ownerId.trim()) {
    params.set("owner_id", ownerId.trim());
  }
  return httpRequest<ImageLibraryResponse>(`/api/image-library?${params.toString()}`);
}

export async function fetchImageLibraryItem(id: number, options: { includeReferences?: boolean } = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.includeReferences) params.set("includeReferences", "1");
  return httpRequest<ImageLibraryItem>(`/api/image-library/${id}?${params.toString()}`);
}

export async function updateImageLibraryItem(id: number, body: { favorite?: boolean; deleted?: boolean }) {
  return httpRequest<ImageLibraryItem>(`/api/image-library/${id}`, {
    method: "PATCH",
    body,
  });
}

export async function bulkDeleteImageLibraryItems(ids: number[]) {
  return httpRequest<{ requested: number; deleted: number; missing: number }>("/api/image-library/bulk-delete", {
    method: "POST",
    body: { ids },
  });
}

export async function downloadImageLibraryZip(body: { ids: number[]; folderName?: string }) {
  try {
    const response = await request.request<Blob>({
      url: "/api/image-library/download-zip",
      method: "POST",
      responseType: "blob",
      data: {
        ids: body.ids,
        folder_name: body.folderName || "历史图库",
      },
    });
    return response.data;
  } catch (error) {
    throw new Error((await downloadErrorMessage(error)) || "打包下载失败");
  }
}

export async function downloadImageLibraryItem(id: number) {
  try {
    const response = await request.request<Blob>({
      url: `/api/image-library/${encodeURIComponent(String(id))}/download`,
      method: "GET",
      responseType: "blob",
    });
    const filename = filenameFromContentDisposition(response.headers["content-disposition"]) || `image-${id}.png`;
    return { blob: response.data, filename };
  } catch (error) {
    throw new Error((await downloadErrorMessage(error)) || "下载图片失败");
  }
}

export async function fetchProducts(options: { q?: string; status?: string } = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.q?.trim()) params.set("q", options.q.trim());
  if (options.status !== undefined) params.set("status", options.status);
  return httpRequest<{ items: BusinessProduct[]; total: number }>(`/api/products?${params.toString()}`);
}

export async function createProduct(body: Partial<BusinessProduct>) {
  return httpRequest<BusinessProduct>("/api/products", {
    method: "POST",
    body,
  });
}

export async function updateProduct(id: number, body: Partial<BusinessProduct>) {
  return httpRequest<BusinessProduct>(`/api/products/${id}`, {
    method: "PATCH",
    body,
  });
}

export async function archiveProduct(id: number) {
  return httpRequest<BusinessProduct>(`/api/products/${id}`, {
    method: "DELETE",
  });
}

export async function uploadProductReference(productId: number, file: File) {
  const formData = new FormData();
  formData.append("image", file);
  return httpRequest<ProductReference>(`/api/products/${productId}/references`, {
    method: "POST",
    body: formData,
  });
}

export async function fetchPromptTemplates(options: {
  q?: string;
  category?: string;
  includeDisabled?: boolean;
} = {}) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  if (options.q?.trim()) params.set("q", options.q.trim());
  if (options.category) params.set("category", options.category);
  if (options.includeDisabled) params.set("include_disabled", "true");
  return httpRequest<{ items: PromptTemplate[]; total: number }>(`/api/prompt-templates?${params.toString()}`);
}

export async function createPromptTemplate(body: Partial<PromptTemplate>) {
  return httpRequest<PromptTemplate>("/api/prompt-templates", {
    method: "POST",
    body,
  });
}

export async function updatePromptTemplate(id: number, body: Partial<PromptTemplate>) {
  return httpRequest<PromptTemplate>(`/api/prompt-templates/${id}`, {
    method: "PATCH",
    body,
  });
}

export async function deletePromptTemplate(id: number) {
  return httpRequest<{ ok: boolean; deleted: boolean; id: number; name: string }>(`/api/prompt-templates/${id}`, {
    method: "DELETE",
  });
}

export async function fetchAuditLogs(limit = 100) {
  return httpRequest<{ items: AuditLogItem[]; total: number }>(`/api/audit-logs?limit=${limit}&_t=${Date.now()}`);
}
