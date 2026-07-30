import { httpRequest, request } from "@/lib/request";

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
    owner_pending_limit?: number | string;
    [key: string]: unknown;
  };
  image_retention_days?: number | string;
  image_poll_timeout_secs?: number | string;
  image_timeout_retry_secs?: number | string;
  image_storage?: {
    enabled: boolean;
    mode: "local" | "webdav" | "minio" | "qiniu" | "both";
    provider?: "webdav" | "minio" | "qiniu";
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
    provider?: "qiniu";
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
  data?: Array<{ b64_json?: string; url?: string; revised_prompt?: string }>;
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

export type ImageLibraryItem = {
  id: number;
  task_id: string;
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

export type AuditLogItem = {
  id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id?: string;
  detail?: string;
  created_at: string;
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
  token: string;
};

export type CurrentUserResponse = {
  ok: boolean;
  role: AuthRole;
  subject_id: string;
  username?: string;
  name: string;
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
  queued_tasks: number;
  running_tasks: number;
  stale_running_tasks: number;
  active_slots: number;
  slot_limit: number;
  active_workers: number;
  worker_concurrency: number;
  local_concurrency_limit: number;
  configured_total_concurrency: number;
  total_concurrency: number;
  owner_concurrency: number;
  owner_pending_limit: number;
  stale_running_timeout_secs: number;
  worker_heartbeat_secs: number;
  owner_activity?: MonitoringQueueOwnerActivity[];
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

export async function logout() {
  return httpRequest<{ ok: boolean }>("/api/auth/logout", {
    method: "POST",
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
) {
  return httpRequest<ImageTask>("/api/image-tasks/generations", {
    method: "POST",
    body: {
      client_task_id: clientTaskId,
      prompt,
      ...(model ? { model } : {}),
      ...(size ? { size } : {}),
      quality,
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
  const params = new URLSearchParams();
  if (ids.length > 0) {
    params.set("ids", ids.join(","));
  }
  params.set("_t", String(Date.now()));
  return httpRequest<ImageTaskListResponse>(`/api/image-tasks?${params.toString()}`);
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

export async function downloadImageTaskZip(body: {
  folderName: string;
  items: Array<{ url?: string; b64Json?: string; filename: string }>;
}) {
  const response = await request.request<Blob>({
    url: "/api/image-tasks/download-zip",
    method: "POST",
    responseType: "blob",
    data: {
      folder_name: body.folderName,
      items: body.items.map((item) => ({
        url: item.url || "",
        b64_json: item.b64Json || "",
        filename: item.filename,
      })),
    },
  });
  return response.data;
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

export async function fetchImageLibrary(options: {
  limit?: number;
  offset?: number;
  cursor?: ImageLibraryCursor | null;
  q?: string;
  productId?: number;
  templateId?: number;
  favorite?: boolean;
} = {}) {
  const { limit = 80, offset = 0, cursor = null, q = "", productId = 0, templateId = 0, favorite = false } = options;
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
  return httpRequest<ImageLibraryResponse>(`/api/image-library?${params.toString()}`);
}

export async function updateImageLibraryItem(id: number, body: { favorite?: boolean; deleted?: boolean }) {
  return httpRequest<ImageLibraryItem>(`/api/image-library/${id}`, {
    method: "PATCH",
    body,
  });
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
