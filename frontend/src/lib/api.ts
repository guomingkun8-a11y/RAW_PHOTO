import { httpRequest, request } from "@/lib/request";
import { webConfig } from "@/lib/config";
import { getStoredAuthKey } from "@/stores/auth";

export type ImageModel = string;
export type AuthRole = "admin" | "user";

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
  }>;
  error?: string;
  progress?: string;
  elapsed_secs?: number;
  duration_ms?: number;
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
    inputTokens?: number;
    outputTokens?: number;
    totalTokens?: number;
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
  model?: ImageModel;
  size?: string;
  quality?: string;
  preserve_subject: boolean;
  enabled: boolean;
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

type ImageConversationListResponse = {
  items: ImageConversationApiPayload[];
  total: number;
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
  queued_tasks: number;
  running_tasks: number;
  active_tasks: number;
  last_login_at?: string;
  last_seen_at?: string;
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

export type MonitoringLatencySummary = {
  sample_size: number;
  average_ms: number;
  p95_ms: number;
  max_ms: number;
};

export type MonitoringStageLatencySummary = {
  upload: MonitoringLatencySummary;
  queue: MonitoringLatencySummary;
  generation: MonitoringLatencySummary;
  save: MonitoringLatencySummary;
};

export type MonitoringSummary = {
  online_users: number;
  active_sessions: number;
  total_success: number;
  total_failed: number;
  total_users: number;
  online_window_minutes: number;
  task_queue: MonitoringQueueSummary;
  agent_queue?: MonitoringAgentQueueSummary;
  task_latency: MonitoringLatencySummary;
  stage_latency: MonitoringStageLatencySummary;
  users: MonitoringUserStat[];
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

export async function fetchMonitoringSummary() {
  return httpRequest<MonitoringSummary>(`/api/monitoring/summary?_t=${Date.now()}`);
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
) {
  return httpRequest<ImageTask>("/api/image-tasks/generations", {
    method: "POST",
    body: {
      client_task_id: clientTaskId,
      prompt,
      ...(model ? { model } : {}),
      ...(size ? { size } : {}),
      quality,
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

export async function fetchImageConversationsRemote() {
  return httpRequest<ImageConversationListResponse>(`/api/image-conversations?_t=${Date.now()}`);
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
          image_index: item.imageIndex || 0,
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

export async function disablePromptTemplate(id: number) {
  return httpRequest<PromptTemplate>(`/api/prompt-templates/${id}`, {
    method: "DELETE",
  });
}

export async function fetchAuditLogs(limit = 100) {
  return httpRequest<{ items: AuditLogItem[]; total: number }>(`/api/audit-logs?limit=${limit}&_t=${Date.now()}`);
}
