import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { toast } from "vue-sonner";

import {
  cancelImageTask,
  createImageEditTask,
  createImageGenerationTask,
  fetchImageTasks,
  fetchModels,
  fetchPromptTemplates,
  fetchSettingsConfig,
  preuploadImageReferences,
  reportImageFailure,
  resumeImagePoll,
  type ImageModel,
  type ImageTask,
  type PromptTemplate,
  type ReferenceUploadItem,
  type SettingsConfig,
} from "@/lib/api";
import { BUILTIN_IMAGE_MODELS, filterImageModels, formatImageModel, isImageModel } from "@/lib/image-models";
import {
  clearImageConversations,
  deleteImageConversation,
  getImageConversationStats,
  listImageConversations,
  renameImageConversation,
  saveImageConversation,
  saveImageConversations,
  type ImageBatchReplacePlan,
  type ImageConversation,
  type ImageConversationMode,
  type ImageTurn,
  type StoredImage,
  type StoredReferenceImage,
} from "@/stores/image-conversations";

const ACTIVE_CONVERSATION_STORAGE_KEY = "gmkraw:image_single_active_conversation_id";
const IMAGE_RATIO_STORAGE_KEY = "gmkraw:image_last_ratio";
const IMAGE_TIER_STORAGE_KEY = "gmkraw:image_last_tier";
const IMAGE_QUALITY_STORAGE_KEY = "gmkraw:image_last_quality";
const IMAGE_MODEL_STORAGE_KEY = "gmkraw:image_last_model";
const PRESERVE_SUBJECT_STORAGE_KEY = "gmkraw:image_preserve_subject";
const IMAGE_COUNT_STORAGE_KEY = "gmkraw:image_last_count";
const IMAGE_COUNT_DEFAULT_MIGRATION_KEY = "gmkraw:image_count_default_one_applied";
const DEFAULT_IMAGE_COUNT = "1";
const REFERENCE_UPLOAD_CONCURRENCY = 2;
const SCENE_IMAGE_OPTIMIZE_MIN_BYTES = 768 * 1024;
const SCENE_IMAGE_MAX_DIMENSION = 2048;
const SCENE_IMAGE_WEBP_QUALITY = 0.92;
const DEFAULT_OWNER_CONCURRENCY = 3;
const DEFAULT_OWNER_PENDING_LIMIT = 30;
const BATCH_REPLACE_BASE_PROMPT = [
  "以第一张参考图作为唯一商品主体，逐张处理第二张参考图。",
  "把第二张参考图中的原商品替换为第一张参考图里的商品。",
  "保持第二张参考图的背景、构图、光线、透视、人物、道具、版式和画幅不变。",
  "保持第一张商品的包装结构、Logo、可见文字、颜色、材质和比例一致。",
  "只替换商品，不改变场景中的其他元素，不新增无关文案。",
].join("\n");
const HIGH_RISK_CLAIM_REPLACEMENTS: Array<[RegExp, string]> = [
  [/杀菌率\s*99(?:\.\d+)?\s*%?/gi, "清洁表现"],
  [/99(?:\.\d+)?\s*%?\s*杀菌率?/gi, "清洁表现"],
  [/杀灭细菌|灭活病毒|抗病毒|医用级|医疗级|消毒|杀菌|医用/gi, "清洁护理"],
];
const IMAGE_PROMPT_COMPLIANCE_GUARD = "合规约束：画面文字只保留中性产品信息；不要生成医疗、消杀、抗微生物、病毒相关或等级背书类功效宣称，不要新增功效徽章、百分比承诺或认证标识。";
const IMAGE_LAYOUT_GUARD_PREFIX = "画面结构约束：";
const IMAGE_SINGLE_LAYOUT_GUARD = `${IMAGE_LAYOUT_GUARD_PREFIX}只生成一张完整独立图片，只展示一个主场景，不要拼图、不要分屏、不要九宫格、不要多面板，不要把多个场景或多张成品图合在同一张画布里。`;
const IMAGE_MULTI_COUNT_DEFAULT = 4;
const IMAGE_MULTI_COUNT_MAX = 8;

const activeImageTurnQueueIds = new Set<string>();
const canceledImageTaskIds = new Set<string>();
const reportedFailureTaskIds = new Set<string>();
const submittedImageTaskIds = new Set<string>();

type DeleteConfirm =
  | { type: "one"; id: string }
  | { type: "prompt"; conversationId: string; turnId: string }
  | { type: "results"; conversationId: string; turnId: string }
  | { type: "all" };

type VisibleFailureReport = {
  taskId: string;
  error?: string;
  mode?: ImageConversationMode;
  model?: ImageModel;
  productId?: number;
  templateId?: number;
};

function clampImageCount(value: string) {
  return String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}
function parseImageSize(size: string) {
  const match = size.match(/^(\d+)x(\d+)$/);
  return match ? { width: match[1], height: match[2] } : { width: "1024", height: "1024" };
}
function createId() { return `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function imageTurnQueueKey(conversationId: string, turnId: string) { return `${conversationId}:${turnId}`; }
function shouldRunImageTurn(turn: ImageTurn) {
  return !turn.resultsDeleted && (turn.status === "queued" || turn.status === "generating") && turn.images.some((image) => image.status === "loading");
}
function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  return trimmed.length <= 12 ? trimmed : `${trimmed.slice(0, 12)}...`;
}
function buildBatchReplacePrompt(prompt: string) {
  return prompt.trim() ? `${BATCH_REPLACE_BASE_PROMPT}\n\n用户补充要求：\n${prompt.trim()}` : BATCH_REPLACE_BASE_PROMPT;
}
function stripHighRiskClaims(prompt: string) {
  let cleaned = prompt;
  for (const [pattern, replacement] of HIGH_RISK_CLAIM_REPLACEMENTS) cleaned = cleaned.replace(pattern, replacement);
  return cleaned.trim();
}
function appendPromptGuard(prompt: string, guard: string, marker: string) {
  const cleaned = prompt.trim();
  if (cleaned.includes(marker)) return cleaned;
  return `${cleaned}\n\n${guard}`.trim();
}
function buildImageLayoutGuard(imageIndex: number, imageCount: number, batchReplace = false) {
  const total = Math.max(1, Math.floor(Number(imageCount) || 1));
  if (total <= 1) return IMAGE_SINGLE_LAYOUT_GUARD;
  const current = Math.min(total, Math.max(1, Math.floor(Number(imageIndex) || 0) + 1));
  if (batchReplace) {
    return `${IMAGE_LAYOUT_GUARD_PREFIX}这是第 ${current}/${total} 张独立批量替换结果；本次只处理当前这一张参考图并输出一张完整图片，不要拼图、不要分屏、不要九宫格、不要多面板，不要把其他参考图或其他场景放进同一张画布里。`;
  }
  return `${IMAGE_LAYOUT_GUARD_PREFIX}这是第 ${current}/${total} 张独立成品图；本次只生成这一张图，可以选择一个不同场景或卖点表达，但不要拼图、不要分屏、不要九宫格、不要多面板，不要把其他编号或其他场景放进同一张画布里，画面中也不要写“第${current}张”。`;
}
function buildCompliantImagePrompt(prompt: string, imageIndex = 0, imageCount = 1, batchReplace = false) {
  const cleaned = stripHighRiskClaims(prompt);
  const withLayoutGuard = appendPromptGuard(cleaned, buildImageLayoutGuard(imageIndex, imageCount, batchReplace), IMAGE_LAYOUT_GUARD_PREFIX);
  return appendPromptGuard(withLayoutGuard, IMAGE_PROMPT_COMPLIANCE_GUARD, IMAGE_PROMPT_COMPLIANCE_GUARD);
}
function chineseCount(value: string) {
  const digits: Record<string, number> = { 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  return digits[value] || 0;
}
function parseRequestedImageCount(prompt: string) {
  const arabic = prompt.match(/(?:生成|出|做|来|给我|变|制作|设计|产出|拆成|分成)?\s*([2-9]|1\d|[2-9]\d)\s*(?:张|幅)(?:图|图片|场景|版本|方案)?/);
  if (arabic) return Math.min(IMAGE_MULTI_COUNT_MAX, Math.max(2, Number(arabic[1])));
  const arabicGroup = prompt.match(/(?:生成|出|做|来|给我|变|制作|设计|产出|拆成|分成)?\s*([2-9]|1\d|[2-9]\d)\s*(?:组|个)(?:不同|独立|单独|分开|不同卖点)?\s*(?:图|图片|场景|版本|方案)/);
  if (arabicGroup) return Math.min(IMAGE_MULTI_COUNT_MAX, Math.max(2, Number(arabicGroup[1])));
  const chinese = prompt.match(/(?:生成|出|做|来|给我|变|制作|设计|产出|拆成|分成)?\s*([二两三四五六七八九十])\s*(?:张|幅)(?:图|图片|场景|版本|方案)?/);
  if (chinese) return Math.min(IMAGE_MULTI_COUNT_MAX, Math.max(2, chineseCount(chinese[1])));
  const chineseGroup = prompt.match(/(?:生成|出|做|来|给我|变|制作|设计|产出|拆成|分成)?\s*([二两三四五六七八九十])\s*(?:组|个)(?:不同|独立|单独|分开|不同卖点)?\s*(?:图|图片|场景|版本|方案)/);
  if (chineseGroup) return Math.min(IMAGE_MULTI_COUNT_MAX, Math.max(2, chineseCount(chineseGroup[1])));
  return 0;
}
function hasMultiImageIntent(prompt: string) {
  const text = prompt.replace(/\s+/g, "");
  return (
    /(?:生成|出|做|来|给我|变|制作|设计|产出|拆成|分成).{0,16}(?:多张|几张|多幅|几幅|多个版本|多种场景|不同场景|不同卖点|不同版本)/.test(text) ||
    /(?:每张|每一张).{0,16}(?:不同|分开|单独|独立|场景|卖点)/.test(text) ||
    /(?:不同场景|不同卖点|不同版本).{0,16}(?:每张|每一张|分开|单独|独立)/.test(text)
  );
}
function resolveImageCountFromPrompt(prompt: string, selectedCount: number) {
  const current = Math.max(1, Math.floor(Number(selectedCount) || 1));
  if (current > 1) return current;
  const requested = parseRequestedImageCount(prompt);
  if (requested > 1) return requested;
  return hasMultiImageIntent(prompt) ? IMAGE_MULTI_COUNT_DEFAULT : current;
}
function sortConversations(items: ImageConversation[]) { return [...items].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)); }
function pickFallbackConversationId(items: ImageConversation[]) {
  const active = items.find((conversation) => conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"));
  return active?.id || items[0]?.id || null;
}
function sleep(ms: number) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
function positiveInteger(value: unknown, fallback: number) {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
function taskSubmissionLimits(config: SettingsConfig | null) {
  const queue = config?.image_task_queue;
  const ownerConcurrency = positiveInteger(queue?.owner_concurrency, DEFAULT_OWNER_CONCURRENCY);
  const ownerPendingLimit = positiveInteger(queue?.owner_pending_limit, DEFAULT_OWNER_PENDING_LIMIT);
  const ownerReserve = Math.max(2, ownerConcurrency);
  const ownerWindow = Math.max(1, ownerPendingLimit - ownerReserve);
  return {
    ownerWindow,
    turnWindow: Math.max(1, Math.min(ownerWindow, Math.max(8, ownerConcurrency * 4))),
  };
}
function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}
async function fileToReference(file: File): Promise<StoredReferenceImage> {
  return { name: file.name, type: file.type || "image/png", dataUrl: await readFileAsDataUrl(file) };
}
function dataUrlToFile(dataUrl: string, fileName: string, mimeType?: string) {
  const [header, content] = dataUrl.split(",", 2);
  const matchedMime = header.match(/data:(.*?);base64/)?.[1];
  const binary = atob(content || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new File([bytes], fileName, { type: mimeType || matchedMime || "image/png" });
}
function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality?: number) {
  return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));
}
async function optimizeSceneReference(reference: StoredReferenceImage, fallbackName: string) {
  const original = dataUrlToFile(reference.dataUrl, reference.name || fallbackName, reference.type);
  if (original.size < SCENE_IMAGE_OPTIMIZE_MIN_BYTES) return original;
  try {
    const bitmap = await createImageBitmap(original);
    const scale = Math.min(1, SCENE_IMAGE_MAX_DIMENSION / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && original.type === "image/png") { bitmap.close(); return original; }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d", { alpha: original.type === "image/png" });
    if (!context) { bitmap.close(); return original; }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const outputType = original.type === "image/png" ? "image/png" : "image/webp";
    const optimized = await canvasToBlob(canvas, outputType, outputType === "image/webp" ? SCENE_IMAGE_WEBP_QUALITY : undefined);
    if (!optimized || optimized.size >= original.size * 0.95) return original;
    const outputName = outputType === "image/webp" ? original.name.replace(/\.[^.]+$/, "") + ".webp" : original.name;
    return new File([optimized], outputName, { type: outputType });
  } catch {
    return original;
  }
}
async function runWithConcurrency<T>(items: T[], concurrency: number, worker: (item: T, index: number) => Promise<void>) {
  let nextIndex = 0;
  const runWorker = async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      await worker(items[index], index);
    }
  };
  await Promise.all(Array.from({ length: Math.min(Math.max(1, concurrency), items.length) }, runWorker));
}
function isPrivateHost(hostname: string) {
  const host = hostname.trim().toLowerCase();
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(host) || host.endsWith(".local")) return true;
  const parts = host.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 || parts[0] === 127 || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 192 && parts[1] === 168) || (parts[0] === 169 && parts[1] === 254);
}
function isPublicUrl(rawUrl: string) {
  try {
    const url = new URL(rawUrl);
    return ["http:", "https:"].includes(url.protocol) && !isPrivateHost(url.hostname);
  } catch { return false; }
}
async function fetchImageAsFile(url: string, fileName: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("读取结果图失败");
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type || "image/png" });
}
async function storedImageToReference(image: StoredImage, fileName: string) {
  if (image.b64_json) {
    const reference = { name: fileName, type: "image/png", dataUrl: `data:image/png;base64,${image.b64_json}` };
    return { referenceImage: reference, file: dataUrlToFile(reference.dataUrl, reference.name, reference.type) };
  }
  if (!image.url) return null;
  const file = await fetchImageAsFile(image.url, fileName);
  return { referenceImage: { name: file.name, type: file.type || "image/png", dataUrl: await readFileAsDataUrl(file), url: image.url }, file };
}
async function buildReferencePayload(
  images: StoredReferenceImage[],
  turnId: string,
  preparedReferences: Map<string, ReferenceUploadItem> = new Map(),
) {
  const files: File[] = [];
  const urls: string[] = [];
  const preparedItems: ReferenceUploadItem[] = [];
  for (const [index, image] of images.entries()) {
    const prepared = image.dataUrl ? preparedReferences.get(image.dataUrl) : undefined;
    if (prepared?.url) {
      urls.push(prepared.url);
      preparedItems.push(prepared);
      continue;
    }
    const url = String(image.url || "").trim();
    if (url && isPublicUrl(url)) { urls.push(url); continue; }
    if (image.dataUrl) files.push(dataUrlToFile(image.dataUrl, image.name || `${turnId}-${index + 1}.png`, image.type));
    else if (url) files.push(await fetchImageAsFile(url, image.name || `${turnId}-${index + 1}.png`));
  }
  return {
    files,
    urls,
    referenceUploadMs: preparedItems.reduce((total, item) => total + Math.max(0, item.upload_ms || 0), 0),
    referenceCacheHits: preparedItems.filter((item) => item.cached).length,
  };
}

function progressLabel(progress?: string) {
  const value = String(progress || "").trim();
  if (!value) return "";
  if (value.startsWith("retrying")) return "失败后重新排队";
  const labels: Record<string, string> = {
    queued: "等待生成",
    waiting_for_user_concurrency: "等待当前用户并发",
    waiting_for_slot: "等待全局并发",
    uploading: "上传参考图",
    bootstrapping: "初始化上游",
    getting_token: "获取上游账号",
    preparing_conversation: "准备生成任务",
    starting_generation: "正在提交生成",
    image_stream_resolve_start: "上游生成中",
    generating: "上游生成中",
    receiving_image: "正在接收图片",
    stale_requeued: "超时后重新排队",
  };
  return labels[value] || value;
}
function taskDataToStoredImage(image: StoredImage, task: ImageTask): StoredImage {
  if (task.status === "success") {
    const first = task.data?.[0];
    if (!first?.b64_json && !first?.url) return { ...image, taskId: task.id, status: "error", taskStatus: undefined, progress: undefined, error: "未返回图片数据" };
    return { ...image, taskId: task.id, status: "success", taskStatus: undefined, progress: undefined, b64_json: first.b64_json, url: first.url, revised_prompt: first.revised_prompt, error: undefined, durationMs: task.duration_ms };
  }
  if (task.status === "error") return { ...image, taskId: task.id, status: "error", taskStatus: undefined, progress: undefined, error: task.error || "生成失败", durationMs: task.duration_ms };
  if (task.status === "canceled") return { ...image, taskId: task.id, status: "canceled", taskStatus: undefined, progress: undefined, error: task.error || "任务已中止", durationMs: task.duration_ms };
  const taskStatus = task.status === "queued" ? "queued" : task.status === "running" ? "running" : image.taskStatus;
  const batch = task.batch_progress;
  const batchProgress = batch ? `批次 ${batch.completed + batch.failed + batch.canceled}/${batch.total}` : "";
  return { ...image, taskId: task.id, status: "loading", taskStatus, progress: progressLabel(task.progress) || batchProgress || image.progress, error: undefined, startTime: taskStatus === "running" && !image.startTime ? Date.now() : image.startTime, elapsedSecs: taskStatus === "running" && typeof task.elapsed_secs === "number" ? task.elapsed_secs : undefined, elapsedUpdatedAt: typeof task.elapsed_secs === "number" ? Date.now() : undefined };
}
function deriveTurnStatus(turn: ImageTurn): Pick<ImageTurn, "status" | "error"> {
  const loading = turn.images.filter((image) => image.status === "loading").length;
  const failed = turn.images.filter((image) => image.status === "error").length;
  const canceled = turn.images.filter((image) => image.status === "canceled").length;
  const success = turn.images.filter((image) => image.status === "success").length;
  if (loading) return { status: turn.images.some((image) => image.taskStatus === "running") ? "generating" : turn.status === "queued" ? "queued" : "generating", error: undefined };
  if (failed) return { status: "error", error: `其中 ${failed} 张未成功生成` };
  if (canceled) return { status: "canceled", error: undefined };
  if (success) return { status: "success", error: undefined };
  return { status: "success", error: undefined };
}

async function reportFailures(reports: VisibleFailureReport[]) {
  const unique = reports.filter((report) => {
    if (!report.taskId || reportedFailureTaskIds.has(report.taskId)) return false;
    reportedFailureTaskIds.add(report.taskId);
    return true;
  });
  await Promise.allSettled(unique.map((report) => reportImageFailure({ taskId: report.taskId, error: report.error, imageCount: 1, mode: report.mode, model: report.model, productId: report.productId, templateId: report.templateId })));
}

async function syncConversationTasks(items: ImageConversation[]) {
  const taskIds = Array.from(new Set(items.flatMap((conversation) => conversation.turns.flatMap((turn) => turn.resultsDeleted ? [] : turn.images.flatMap((image) => (image.status === "loading" || (image.status === "error" && image.taskId)) && image.taskId ? [image.taskId] : [])))));
  if (!taskIds.length) return items;
  try {
    const taskList = await fetchImageTasks(taskIds);
    const taskMap = new Map(taskList.items.map((task) => [task.id, task]));
    let changed = false;
    const failures: VisibleFailureReport[] = [];
    const normalized = items.map((conversation) => {
      let conversationChanged = false;
      const turns = conversation.turns.map((turn) => {
        let turnChanged = false;
        const images = turn.images.map((image) => {
          if (!image.taskId || (image.status !== "loading" && image.status !== "error")) return image;
          const task = taskMap.get(image.taskId);
          if (!task) return image;
          const next = taskDataToStoredImage(image, task);
          if (image.status !== "error" && next.status === "error") failures.push({ taskId: task.id, error: next.error, mode: turn.mode, model: turn.model, productId: turn.productId, templateId: turn.templateId });
          turnChanged = true;
          return next;
        });
        if (!turnChanged) return turn;
        conversationChanged = true;
        return { ...turn, ...deriveTurnStatus({ ...turn, images }), images };
      });
      if (!conversationChanged) return conversation;
      changed = true;
      return { ...conversation, turns, updatedAt: new Date().toISOString() };
    });
    if (changed) await saveImageConversations(normalized);
    await reportFailures(failures);
    return normalized;
  } catch { return items; }
}

async function recoverHistory(items: ImageConversation[]) {
  let changed = false;
  const normalized = items.map((conversation) => {
    let conversationChanged = false;
    const turns = conversation.turns.map((turn) => {
      if (!["queued", "generating", "error"].includes(turn.status)) return turn;
      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (image.status !== "loading" || image.taskId) return image;
        turnChanged = true;
        return { ...image, status: "error" as const, error: "页面刷新或任务中断，未找到可恢复的任务 ID" };
      });
      if (!turnChanged) return turn;
      changed = true;
      conversationChanged = true;
      return { ...turn, ...deriveTurnStatus({ ...turn, images }), images };
    });
    return conversationChanged ? { ...conversation, turns, updatedAt: new Date().toISOString() } : conversation;
  });
  if (changed) await saveImageConversations(normalized);
  return syncConversationTasks(normalized);
}

function pickImageFiles(options: { directory?: boolean; multiple?: boolean }) {
  return new Promise<File[]>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = options.multiple !== false;
    if (options.directory) { input.setAttribute("webkitdirectory", ""); input.setAttribute("directory", ""); }
    input.style.position = "fixed";
    input.style.left = "-9999px";
    input.addEventListener("change", () => { const files = Array.from(input.files || []); input.remove(); resolve(files); }, { once: true });
    document.body.appendChild(input);
    input.click();
  });
}

export function useImageWorkspace(isAdmin: boolean) {
  const settingsConfig = ref<SettingsConfig | null>(null);
  const imagePrompt = ref("");
  const imageCount = ref(DEFAULT_IMAGE_COUNT);
  const imageRatio = ref("auto");
  const imageTier = ref("1k");
  const imageWidth = ref("1024");
  const imageHeight = ref("1024");
  const imageQuality = ref("auto");
  const imageModel = ref<ImageModel>("gpt-image-2");
  const imageModels = ref<ImageModel[]>([...BUILTIN_IMAGE_MODELS]);
  const promptTemplates = ref<PromptTemplate[]>([]);
  const selectedTemplateId = ref<number | null>(null);
  const referenceImages = ref<StoredReferenceImage[]>([]);
  const batchProductImage = ref<StoredReferenceImage | null>(null);
  const batchFolderImages = ref<StoredReferenceImage[]>([]);
  const preserveSubject = ref(false);
  const conversations = ref<ImageConversation[]>([]);
  const selectedConversationId = ref<string | null>(null);
  const appendToSelectedConversation = ref(false);
  const isSubmitting = ref(false);
  const isLoadingHistory = ref(true);
  const availableQuota = ref("加载中...");
  const historyOpen = ref(false);
  const deleteConfirm = ref<DeleteConfirm | null>(null);
  const timeoutRetry = ref<{ conversationId: string; taskId: string; taskError: string } | null>(null);
  const lightboxOpen = ref(false);
  const lightboxIndex = ref(0);
  const lightboxImages = ref<Array<{ id: string; src: string; name?: string }>>([]);
  let unmounted = false;
  let suppressSelectionAppend = false;

  function resolveAllowedImageModel(model?: ImageModel | string): ImageModel {
    const candidate = String(model || "").trim();
    return candidate && imageModels.value.includes(candidate) && isImageModel(candidate) ? candidate : imageModels.value[0] || "gpt-image-2";
  }

  const isOpenAIRelayEnabled = computed(() => {
    const relay = settingsConfig.value?.openai_relay;
    return Boolean(relay?.enabled && relay.base_url && (relay.has_api_key || relay.api_key || Number(relay.api_key_count || 0) > 0));
  });
  const imageTimeoutRetrySecs = computed(() => Number(settingsConfig.value?.image_timeout_retry_secs || 30));
  const parsedCount = computed(() => Number(clampImageCount(imageCount.value)));
  const selectedConversation = computed(() => conversations.value.find((item) => item.id === selectedConversationId.value) || null);
  const activeTaskCount = computed(() => conversations.value.reduce((sum, conversation) => { const stats = getImageConversationStats(conversation); return sum + stats.queued + stats.running; }, 0));
  const todayGeneratedCount = computed(() => { const today = new Date().toISOString().slice(0, 10); return conversations.value.reduce((total, conversation) => total + conversation.turns.reduce((sum, turn) => sum + (turn.createdAt.startsWith(today) ? turn.images.filter((image) => image.status === "success").length : 0), 0), 0); });
  const totalGeneratedCount = computed(() => conversations.value.reduce((total, conversation) => total + conversation.turns.reduce((sum, turn) => sum + turn.images.filter((image) => image.status === "success").length, 0), 0));
  const displayModel = computed(() => formatImageModel(imageModel.value));
  const deleteConfirmTitle = computed(() => deleteConfirm.value?.type === "all" ? "清空历史记录" : deleteConfirm.value?.type === "prompt" ? "删除提示词记录" : deleteConfirm.value?.type === "results" ? "删除生成结果" : deleteConfirm.value?.type === "one" ? "删除对话" : "");
  const deleteConfirmDescription = computed(() => deleteConfirm.value?.type === "all" ? "确认删除全部图片历史记录吗？删除后无法恢复。" : deleteConfirm.value?.type === "prompt" ? "确认删除这条提示词记录吗？对应生成结果会保留。" : deleteConfirm.value?.type === "results" ? "确认删除这条生成结果吗？对应提示词记录会保留。" : deleteConfirm.value?.type === "one" ? "确认删除这条图片对话吗？删除后无法恢复。" : "");

  function formatConversationTime(value: string) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  async function persistConversation(conversation: ImageConversation) {
    conversations.value = sortConversations([conversation, ...conversations.value.filter((item) => item.id !== conversation.id)]);
    await saveImageConversation(conversation);
  }
  async function updateConversation(conversationId: string, updater: (current: ImageConversation | null) => ImageConversation, persist = true) {
    const current = conversations.value.find((item) => item.id === conversationId) || null;
    const next = updater(current);
    conversations.value = sortConversations([next, ...conversations.value.filter((item) => item.id !== conversationId)]);
    if (persist) await saveImageConversation(next);
  }
  function clearComposer() {
    imagePrompt.value = "";
    referenceImages.value = [];
    batchProductImage.value = null;
    batchFolderImages.value = [];
  }
  function setSelectedConversationId(id: string | null, append: boolean) {
    suppressSelectionAppend = true;
    selectedConversationId.value = id;
    appendToSelectedConversation.value = append;
    void nextTick(() => { suppressSelectionAppend = false; });
  }
  function createDraft() {
    setSelectedConversationId(null, false);
    clearComposer();
  }
  function selectConversation(id: string) {
    setSelectedConversationId(id, true);
  }
  async function loadQuota() { availableQuota.value = isAdmin ? (isOpenAIRelayEnabled.value ? "中转站" : "API") : "--"; }

  async function loadHistory() {
    try {
      const storedCount = localStorage.getItem(IMAGE_COUNT_STORAGE_KEY);
      if (storedCount === "3" && localStorage.getItem(IMAGE_COUNT_DEFAULT_MIGRATION_KEY) !== "true") {
        localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, DEFAULT_IMAGE_COUNT);
        localStorage.setItem(IMAGE_COUNT_DEFAULT_MIGRATION_KEY, "true");
      }
      const storedTier = localStorage.getItem(IMAGE_TIER_STORAGE_KEY);
      imageRatio.value = storedTier === "2k" ? "1:1" : localStorage.getItem(IMAGE_RATIO_STORAGE_KEY) || "1:1";
      imageTier.value = storedTier === "2k" ? "1k" : storedTier || "1k";
      imageQuality.value = localStorage.getItem(IMAGE_QUALITY_STORAGE_KEY) || "auto";
      imageCount.value = clampImageCount(localStorage.getItem(IMAGE_COUNT_STORAGE_KEY) || DEFAULT_IMAGE_COUNT);
      preserveSubject.value = localStorage.getItem(PRESERVE_SUBJECT_STORAGE_KEY) === "true";
      const items = await recoverHistory(await listImageConversations());
      if (unmounted) return;
      conversations.value = items;
      setSelectedConversationId(null, false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取会话记录失败");
    } finally { isLoadingHistory.value = false; }
  }

  async function loadInitialData() {
    const [settings, templateData, models] = await Promise.allSettled([
      fetchSettingsConfig(),
      fetchPromptTemplates(),
      fetchModels(),
    ]);
    settingsConfig.value = settings.status === "fulfilled" ? settings.value.config : null;
    if (templateData.status === "fulfilled") promptTemplates.value = templateData.value.items;
    if (models.status === "fulfilled") imageModels.value = filterImageModels(Array.isArray(models.value.data) ? models.value.data : []);
    const storedModel = localStorage.getItem(IMAGE_MODEL_STORAGE_KEY);
    imageModel.value = storedModel && imageModels.value.includes(storedModel) ? storedModel : imageModels.value[0] || "gpt-image-2";
    await loadQuota();
  }

  async function appendReferenceFiles(files: File[]) {
    if (!files.length) return;
    try {
      const next = await Promise.all(files.map(fileToReference));
      referenceImages.value = [...referenceImages.value, ...next];
    } catch (error) { toast.error(error instanceof Error ? error.message : "读取参考图失败"); }
  }
  function removeReference(index: number) { referenceImages.value = referenceImages.value.filter((_, current) => current !== index); }
  async function pickBatchProduct() {
    try {
      const file = (await pickImageFiles({ multiple: false })).find((item) => item.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|svg)$/i.test(item.name));
      if (!file) return;
      batchProductImage.value = await fileToReference(file);
      preserveSubject.value = true;
      toast.success("已上传主图，批量替换会以这张图作为商品主体");
    } catch (error) { toast.error(error instanceof Error ? error.message : "读取主图失败"); }
  }
  async function pickBatchFolder() {
    try {
      const files = (await pickImageFiles({ directory: true, multiple: true })).filter((item) => item.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|svg)$/i.test(item.name));
      if (!files.length) { toast.error("文件夹里没有可用图片"); return; }
      batchFolderImages.value = await Promise.all(files.map(fileToReference));
      imageCount.value = String(batchFolderImages.value.length);
      preserveSubject.value = true;
      if (!imagePrompt.value.trim()) imagePrompt.value = "把主图商品替换到每张文件夹图片中，保持原图场景、光线、构图和风格不变。";
      toast.success(`已读取 ${files.length} 张文件夹图片`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "读取文件夹失败"); }
  }
  function clearBatch() { batchProductImage.value = null; batchFolderImages.value = []; imageCount.value = DEFAULT_IMAGE_COUNT; toast.success("已清空批量替换素材"); }

  function createLoadingImages(turnId: string, count: number): StoredImage[] {
    return Array.from({ length: count }, (_, index) => ({ id: `${turnId}-${index}`, taskId: `${turnId}-${index}`, status: "loading", startTime: Date.now() }));
  }
  function createBatchLoadingImages(turnId: string, images: StoredReferenceImage[]): StoredImage[] {
    return images.map((image, index) => ({ id: `${turnId}-${index}`, taskId: `${turnId}-${index}`, status: "loading", startTime: Date.now(), sourceImageIndex: index, sourceName: image.name }));
  }

  async function runConversationQueue(conversationId: string, preferredTurnId?: string) {
    const snapshot = conversations.value.find((conversation) => conversation.id === conversationId);
    const activeTurn = snapshot?.turns.find((turn) => (!preferredTurnId || turn.id === preferredTurnId) && shouldRunImageTurn(turn) && !activeImageTurnQueueIds.has(imageTurnQueueKey(conversationId, turn.id)));
    if (!snapshot || !activeTurn) return;
    const queueKey = imageTurnQueueKey(conversationId, activeTurn.id);
    activeImageTurnQueueIds.add(queueKey);
    const preparedReferences = new Map<string, ReferenceUploadItem>();
    const directReferenceDataUrls = new Set<string>();

    const applyTasks = async (tasks: ImageTask[]) => {
      for (const task of tasks) {
        if (task.status === "queued" || task.status === "running") submittedImageTaskIds.add(task.id);
        else submittedImageTaskIds.delete(task.id);
      }
      const taskMap = new Map(tasks.map((task) => [task.id, task]));
      const failures: VisibleFailureReport[] = [];
      await updateConversation(conversationId, (current) => {
        const conversation = current || snapshot;
        const turns = conversation.turns.map((turn) => {
          if (turn.id !== activeTurn.id) return turn;
          const images = turn.images.map((image) => {
            const taskId = image.taskId || image.id;
            if (image.status === "canceled" || canceledImageTaskIds.has(taskId)) return image;
            const task = taskMap.get(taskId);
            if (!task) return image;
            const next = taskDataToStoredImage({ ...image, taskId }, task);
            if (image.status !== "error" && next.status === "error") failures.push({ taskId: task.id, error: next.error, mode: turn.mode, model: turn.model, productId: turn.productId, templateId: turn.templateId });
            return next;
          });
          return { ...turn, ...deriveTurnStatus({ ...turn, images }), images };
        });
        return { ...conversation, updatedAt: new Date().toISOString(), turns };
      });
      await reportFailures(failures);
    };

    const taskReferenceImages = (image: StoredImage) => {
      const sourceImage = activeTurn.batchReplace && typeof image.sourceImageIndex === "number" ? activeTurn.batchReplace.folderImages[image.sourceImageIndex] : undefined;
      return activeTurn.batchReplace && sourceImage ? [activeTurn.batchReplace.productImage, sourceImage] : activeTurn.referenceImages;
    };
    const submitImage = async (image: StoredImage, imageIndex: number) => {
      const taskId = image.taskId || image.id;
      if (canceledImageTaskIds.has(taskId)) return null;
      const payload = await buildReferencePayload(taskReferenceImages(image), taskId, preparedReferences);
      if (activeTurn.mode === "edit" && !payload.files.length && !payload.urls.length) throw new Error("未找到可用于继续编辑的参考图");
      const taskPrompt = buildCompliantImagePrompt(activeTurn.prompt, imageIndex, activeTurn.images.length, Boolean(activeTurn.batchReplace));
      const taskModel = resolveAllowedImageModel(activeTurn.model);
      const task = activeTurn.mode === "edit"
        ? await createImageEditTask(taskId, payload.files, taskPrompt, taskModel, activeTurn.size, activeTurn.quality, payload.urls, activeTurn.preserveSubject === true, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId, activeTurn.id, imageIndex, activeTurn.images.length, payload.referenceUploadMs, payload.referenceCacheHits)
        : await createImageGenerationTask(taskId, taskPrompt, taskModel, activeTurn.size, activeTurn.quality, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId, activeTurn.id, imageIndex, activeTurn.images.length);
      if (canceledImageTaskIds.has(taskId)) await cancelImageTask(taskId).catch(() => undefined);
      return task;
    };

    const submissionFailures = new Map<string, number>();
    const updateWaitingImages = async (taskIds: Set<string>, progress: string) => {
      if (!taskIds.size) return;
      await updateConversation(conversationId, (current) => {
        const conversation = current || snapshot;
        return {
          ...conversation,
          updatedAt: new Date().toISOString(),
          turns: conversation.turns.map((turn) => turn.id !== activeTurn.id ? turn : {
            ...turn,
            images: turn.images.map((image) => taskIds.has(image.taskId || image.id) && image.status === "loading"
              ? { ...image, taskStatus: undefined, progress }
              : image),
          }),
        };
      });
    };

    const failImagesAfterSubmission = async (failures: Array<{ image: StoredImage; message: string }>) => {
      if (!failures.length) return;
      const failureMap = new Map(failures.map(({ image, message }) => [image.taskId || image.id, message]));
      const reports: VisibleFailureReport[] = [];
      await updateConversation(conversationId, (current) => {
        const conversation = current || snapshot;
        const turns = conversation.turns.map((turn) => {
          if (turn.id !== activeTurn.id) return turn;
          const images = turn.images.map((image) => {
            const taskId = image.taskId || image.id;
            const message = failureMap.get(taskId);
            if (!message || image.status !== "loading") return image;
            reports.push({ taskId, error: message, mode: turn.mode, model: turn.model, productId: turn.productId, templateId: turn.templateId });
            return { ...image, status: "error" as const, taskStatus: undefined, progress: undefined, error: message };
          });
          return { ...turn, ...deriveTurnStatus({ ...turn, images }), images };
        });
        return { ...conversation, updatedAt: new Date().toISOString(), turns };
      });
      await reportFailures(reports);
    };

    type ReferenceUploadPlan = { reference: StoredReferenceImage; optimizeScene: boolean };
    const sameReference = (left: StoredReferenceImage, right: StoredReferenceImage) =>
      Boolean(left.dataUrl && right.dataUrl && left.dataUrl === right.dataUrl)
      || Boolean(left.url && right.url && left.url === right.url);
    const referencePreuploadEnabled = () => settingsConfig.value?.image_reference_upload?.enabled !== false;
    const referenceReady = (reference: StoredReferenceImage) => {
      const publicUrl = String(reference.url || "").trim();
      return Boolean(
        (publicUrl && isPublicUrl(publicUrl))
        || (
          reference.dataUrl
          && (!referencePreuploadEnabled() || preparedReferences.has(reference.dataUrl) || directReferenceDataUrls.has(reference.dataUrl))
        ),
      );
    };
    const taskReferencesReady = (image: StoredImage) => activeTurn.mode !== "edit" || taskReferenceImages(image).every(referenceReady);
    const dependentLoadingImages = (reference: StoredReferenceImage) => {
      const latestTurn = conversations.value.find((item) => item.id === conversationId)?.turns.find((turn) => turn.id === activeTurn.id);
      return latestTurn?.images.filter((image) =>
        image.status === "loading"
        && !submittedImageTaskIds.has(image.taskId || image.id)
        && taskReferenceImages(image).some((candidate) => sameReference(candidate, reference)),
      ) || [];
    };
    const setReferenceProgress = async (reference: StoredReferenceImage, progress: string) => {
      await updateWaitingImages(new Set(dependentLoadingImages(reference).map((image) => image.taskId || image.id)), progress);
    };
    const preuploadTurnReferences = async () => {
      if (activeTurn.mode !== "edit") return;
      if (!referencePreuploadEnabled()) return;
      const plans: ReferenceUploadPlan[] = [];
      const seen = new Set<string>();
      const addPlan = (reference: StoredReferenceImage, optimizeScene: boolean) => {
        const publicUrl = String(reference.url || "").trim();
        if (!reference.dataUrl || (publicUrl && isPublicUrl(publicUrl)) || seen.has(reference.dataUrl)) return;
        if (!dependentLoadingImages(reference).length) return;
        seen.add(reference.dataUrl);
        plans.push({ reference, optimizeScene });
      };
      if (activeTurn.batchReplace) {
        addPlan(activeTurn.batchReplace.productImage, false);
        activeTurn.batchReplace.folderImages.forEach((reference) => addPlan(reference, true));
      } else {
        activeTurn.referenceImages.forEach((reference) => addPlan(reference, false));
      }
      if (!plans.length) return;

      let completedUploads = 0;
      const uploadPlan = async (plan: ReferenceUploadPlan, index: number) => {
        const { reference } = plan;
        if (unmounted || !dependentLoadingImages(reference).length) {
          completedUploads += 1;
          return;
        }
        try {
          await setReferenceProgress(reference, `正在上传参考图 ${completedUploads}/${plans.length}`);
          const fallbackName = `${activeTurn.id}-${index + 1}.png`;
          const file = plan.optimizeScene
            ? await optimizeSceneReference(reference, fallbackName)
            : dataUrlToFile(reference.dataUrl, reference.name || fallbackName, reference.type);
          if (unmounted || !dependentLoadingImages(reference).length) {
            completedUploads += 1;
            return;
          }
          let response: Awaited<ReturnType<typeof preuploadImageReferences>> | null = null;
          for (let attempt = 1; attempt <= 2; attempt += 1) {
            try {
              response = await preuploadImageReferences([file]);
              break;
            } catch (error) {
              if (attempt >= 2) throw error;
              await setReferenceProgress(reference, "参考图上传波动，正在自动重试");
              await sleep(1200);
            }
          }
          const item = response?.items[0];
          if (!item?.url) throw new Error("参考图预上传结果不完整");
          preparedReferences.set(reference.dataUrl, item);
          completedUploads += 1;
          await setReferenceProgress(reference, `参考图已就绪 ${completedUploads}/${plans.length}，等待入队`);
        } catch (error) {
          completedUploads += 1;
          if (reference.dataUrl) directReferenceDataUrls.add(reference.dataUrl);
          await setReferenceProgress(reference, "参考图预上传失败，已改为随任务直传");
        }
      };

      const productPlan = activeTurn.batchReplace
        ? plans.find((plan) => sameReference(plan.reference, activeTurn.batchReplace!.productImage))
        : undefined;
      if (productPlan) await uploadPlan(productPlan, plans.indexOf(productPlan));
      const remainingPlans = productPlan ? plans.filter((plan) => plan !== productPlan) : plans;
      await runWithConcurrency(remainingPlans, REFERENCE_UPLOAD_CONCURRENCY, async (plan) => {
        await uploadPlan(plan, plans.indexOf(plan));
      });
    };

    const submitNextImages = async () => {
      const latestTurn = conversations.value.find((item) => item.id === conversationId)?.turns.find((turn) => turn.id === activeTurn.id);
      if (!latestTurn) return 0;
      const loadingImages = latestTurn.images.filter((image) => image.status === "loading");
      const limits = taskSubmissionLimits(settingsConfig.value);
      const turnSubmittedCount = loadingImages.filter((image) => submittedImageTaskIds.has(image.taskId || image.id)).length;
      const available = Math.min(limits.turnWindow - turnSubmittedCount, limits.ownerWindow - submittedImageTaskIds.size);
      if (available <= 0) return 0;

      const selected = loadingImages
        .filter((image) => !submittedImageTaskIds.has(image.taskId || image.id) && !canceledImageTaskIds.has(image.taskId || image.id) && taskReferencesReady(image))
        .slice(0, available);
      if (!selected.length) return 0;
      selected.forEach((image) => submittedImageTaskIds.add(image.taskId || image.id));
      await updateWaitingImages(new Set(selected.map((image) => image.taskId || image.id)), "正在分批入队");

      const settled = await Promise.allSettled(selected.map((image) => submitImage(image, latestTurn.images.indexOf(image))));
      const tasks: ImageTask[] = [];
      const permanentFailures: Array<{ image: StoredImage; message: string }> = [];
      const retryTaskIds = new Set<string>();
      settled.forEach((result, index) => {
        const image = selected[index];
        const taskId = image.taskId || image.id;
        if (result.status === "fulfilled" && result.value) {
          tasks.push(result.value);
          submissionFailures.delete(taskId);
          return;
        }
        submittedImageTaskIds.delete(taskId);
        if (result.status === "fulfilled") return;
        const message = result.reason instanceof Error ? result.reason.message : "任务入队失败";
        if (message.includes("user task queue is full")) {
          retryTaskIds.add(taskId);
          return;
        }
        const attempts = (submissionFailures.get(taskId) || 0) + 1;
        submissionFailures.set(taskId, attempts);
        if (attempts >= 3) permanentFailures.push({ image, message });
        else retryTaskIds.add(taskId);
      });
      if (tasks.length) await applyTasks(tasks);
      if (retryTaskIds.size) await updateWaitingImages(retryTaskIds, "暂未入队，等待空位后重试");
      await failImagesAfterSubmission(permanentFailures);
      return selected.length;
    };

    let uploadsFinished = activeTurn.mode !== "edit";
    let uploadPromise: Promise<void> = Promise.resolve();

    try {
      if (activeTurn.mode === "edit" && !activeTurn.referenceImages.length && !activeTurn.batchReplace) throw new Error("未找到可用于继续编辑的参考图");
      const initialTaskIds = activeTurn.images.flatMap((image) => image.status === "loading" && image.taskId ? [image.taskId] : []);
      if (initialTaskIds.length) {
        try {
          const existing = await fetchImageTasks(initialTaskIds);
          existing.items.forEach((task) => {
            if (task.status === "queued" || task.status === "running") submittedImageTaskIds.add(task.id);
          });
          existing.missing_ids.forEach((taskId) => submittedImageTaskIds.delete(taskId));
          if (existing.items.length) await applyTasks(existing.items);
          if (existing.missing_ids.length) await updateWaitingImages(new Set(existing.missing_ids), "等待分批入队");
        } catch {
          // Submitting deterministic task IDs below is idempotent if this recovery probe is temporarily unavailable.
        }
      }
      const turnAfterRecovery = conversations.value.find((item) => item.id === conversationId)?.turns.find((turn) => turn.id === activeTurn.id);
      const hasUnsubmittedImages = turnAfterRecovery?.images.some((image) => image.status === "loading" && !submittedImageTaskIds.has(image.taskId || image.id));
      if (hasUnsubmittedImages && activeTurn.mode === "edit") {
        uploadsFinished = false;
        uploadPromise = preuploadTurnReferences().finally(() => { uploadsFinished = true; });
      }
      let consecutiveErrors = 0;
      const retryingIds = new Set<string>();
      while (!unmounted) {
        await submitNextImages();
        const latestTurn = conversations.value.find((item) => item.id === conversationId)?.turns.find((turn) => turn.id === activeTurn.id);
        const loadingImages = latestTurn?.images.filter((image) => image.status === "loading") || [];
        if (!loadingImages.length) break;
        const submittedIds = loadingImages.flatMap((image) => image.taskId && submittedImageTaskIds.has(image.taskId) ? [image.taskId] : []);
        if (!submittedIds.length && uploadsFinished) {
          const unresolved = loadingImages.filter((image) => !taskReferencesReady(image));
          if (unresolved.length) {
            await failImagesAfterSubmission(unresolved.map((image) => ({ image, message: "参考图未能生成可用的公开地址" })));
            continue;
          }
        }
        await sleep(submittedIds.length ? 2000 : 500);
        if (!submittedIds.length) continue;
        try {
          const taskList = await fetchImageTasks(submittedIds);
          consecutiveErrors = 0;
          const timeoutTask = !isOpenAIRelayEnabled.value ? taskList.items.find((task) => task.status === "error" && task.error?.includes("超时") && task.conversation_id && !retryingIds.has(task.id)) : undefined;
          if (timeoutTask && timeoutTask.conversation_id) {
            retryingIds.add(timeoutTask.id);
            timeoutRetry.value = { conversationId: timeoutTask.conversation_id, taskId: timeoutTask.id, taskError: timeoutTask.error || "生图超时" };
            await applyTasks([timeoutTask]);
          } else if (taskList.items.length) await applyTasks(taskList.items);
          if (taskList.missing_ids.length) {
            taskList.missing_ids.forEach((taskId) => submittedImageTaskIds.delete(taskId));
            await updateWaitingImages(new Set(taskList.missing_ids), "任务记录未找到，正在重新入队");
          }
        } catch (error) {
          consecutiveErrors += 1;
          if (consecutiveErrors >= 10) throw error;
        }
      }
      await uploadPromise;
      await loadQuota();
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成图片失败";
      const failures: VisibleFailureReport[] = [];
      await updateConversation(conversationId, (current) => {
        const conversation = current || snapshot;
        return { ...conversation, updatedAt: new Date().toISOString(), turns: conversation.turns.map((turn) => turn.id !== activeTurn.id ? turn : { ...turn, status: "error", error: message, images: turn.images.map((image) => { if (image.status !== "loading") return image; failures.push({ taskId: image.taskId || image.id, error: message, mode: turn.mode, model: turn.model, productId: turn.productId, templateId: turn.templateId }); return { ...image, status: "error", error: message }; }) }) };
      });
      await reportFailures(failures);
      toast.error(message);
    } finally {
      activeImageTurnQueueIds.delete(queueKey);
      scanQueues();
    }
  }

  function scanQueues() {
    for (const conversation of conversations.value) {
      for (const turn of conversation.turns) {
        if (shouldRunImageTurn(turn) && !activeImageTurnQueueIds.has(imageTurnQueueKey(conversation.id, turn.id))) void runConversationQueue(conversation.id, turn.id);
      }
    }
  }

  async function submit() {
    if (isSubmitting.value) return;
    const rawPrompt = imagePrompt.value.trim();
    const prompt = stripHighRiskClaims(rawPrompt);
    const isBatch = Boolean(batchProductImage.value && batchFolderImages.value.length);
    if (!prompt && !isBatch) { toast.error("请输入提示词"); return; }
    if (batchFolderImages.value.length && !batchProductImage.value) { toast.error("请先上传要替换进去的主图"); return; }
    if (batchProductImage.value && !batchFolderImages.value.length) { toast.error("请先上传包含场景图的文件夹"); return; }
    isSubmitting.value = true;
    try {
      const target = appendToSelectedConversation.value && selectedConversationId.value ? conversations.value.find((item) => item.id === selectedConversationId.value) || null : null;
      const now = new Date().toISOString();
      const conversationId = target?.id || createId();
      const turnId = createId();
      const batchReplace: ImageBatchReplacePlan | undefined = isBatch && batchProductImage.value ? { productImage: batchProductImage.value, folderImages: batchFolderImages.value } : undefined;
      const effectiveReferences = batchReplace ? [batchReplace.productImage, ...batchReplace.folderImages] : referenceImages.value;
      const mode: ImageConversationMode = effectiveReferences.length ? "edit" : "generate";
      const effectivePrompt = batchReplace ? buildBatchReplacePrompt(prompt) : prompt;
      const selectedCount = parsedCount.value;
      const count = batchReplace ? batchReplace.folderImages.length : resolveImageCountFromPrompt(prompt, selectedCount);
      const submitModel = resolveAllowedImageModel(imageModel.value);
      imageModel.value = submitModel;
      const turn: ImageTurn = {
        id: turnId,
        prompt: effectivePrompt,
        model: submitModel,
        mode,
        referenceImages: mode === "edit" ? effectiveReferences : [],
        batchReplace,
        preserveSubject: mode === "edit" && (preserveSubject.value || Boolean(batchReplace)),
        count,
        size: `${imageWidth.value || 1024}x${imageHeight.value || 1024}`,
        ratio: imageRatio.value,
        tier: imageTier.value,
        quality: imageQuality.value,
        templateId: selectedTemplateId.value || undefined,
        images: batchReplace ? createBatchLoadingImages(turnId, batchReplace.folderImages) : createLoadingImages(turnId, count),
        createdAt: now,
        status: "queued",
      };
      const conversation: ImageConversation = target ? { ...target, updatedAt: now, turns: [...target.turns, turn] } : { id: conversationId, title: buildConversationTitle(batchReplace ? `批量换商品 ${batchReplace.folderImages.length} 张` : prompt), createdAt: now, updatedAt: now, turns: [turn] };
      setSelectedConversationId(conversationId, false);
      clearComposer();
      await persistConversation(conversation);
      void runConversationQueue(conversationId, turnId);
      if (batchReplace) toast.success(`已创建批量替换任务：${batchReplace.folderImages.length} 张图`);
      else if (target) toast.success("已追加到选中的图片任务");
      else toast.success("已创建新图片任务并开始处理");
      if (!batchReplace && selectedCount === 1 && count > 1) {
        toast.info(`检测到多张独立图片需求，本次已按 ${count} 张独立任务生成`);
      }
      if (rawPrompt && rawPrompt !== prompt) toast.info("已自动替换高风险功效宣称，避免生成违规宣传文字");
    } finally {
      isSubmitting.value = false;
    }
  }

  async function regenerateTurn(turnId: string) {
    const conversation = selectedConversation.value;
    const source = conversation?.turns.find((turn) => turn.id === turnId);
    if (!conversation || !source || !source.prompt.trim()) return;
    const now = new Date().toISOString();
    const nextId = createId();
    const batchReplace = source.batchReplace;
    const count = batchReplace ? batchReplace.folderImages.length : Math.max(1, source.count || source.images.length || 1);
    const nextTurn: ImageTurn = { ...source, id: nextId, model: resolveAllowedImageModel(source.model), createdAt: now, status: "queued", error: undefined, images: batchReplace ? createBatchLoadingImages(nextId, batchReplace.folderImages) : createLoadingImages(nextId, count), count };
    await persistConversation({ ...conversation, updatedAt: now, turns: [...conversation.turns, nextTurn] });
    void runConversationQueue(conversation.id, nextId);
    toast.success("已开始重新生成");
  }

  async function retryImage(turnId: string, imageId: string) {
    const conversation = selectedConversation.value;
    if (!conversation) return;
    const turn = conversation.turns.find((item) => item.id === turnId);
    if (!turn) return;
    const retryId = `${turnId}-${createId()}`;
    const next = { ...conversation, updatedAt: new Date().toISOString(), turns: conversation.turns.map((item) => item.id !== turnId ? item : { ...item, status: "queued" as const, error: undefined, images: item.images.map((image) => image.id !== imageId ? image : { id: retryId, taskId: retryId, status: "loading" as const, sourceImageIndex: image.sourceImageIndex, sourceName: image.sourceName }) }) };
    await persistConversation(next);
    void runConversationQueue(conversation.id, turnId);
  }

  async function cancelTurn(turnId: string) {
    const conversation = selectedConversation.value;
    const turn = conversation?.turns.find((item) => item.id === turnId);
    if (!conversation || !turn) return;
    const loadingIds = turn.images.flatMap((image) => image.status === "loading" && image.taskId ? [image.taskId] : []);
    if (!loadingIds.length) return;
    const taskIds = new Set(loadingIds);
    loadingIds.forEach((id) => { canceledImageTaskIds.add(id); submittedImageTaskIds.delete(id); });
    await updateConversation(conversation.id, (current) => {
      const base = current || conversation;
      return { ...base, updatedAt: new Date().toISOString(), turns: base.turns.map((item) => item.id !== turnId ? item : { ...item, ...deriveTurnStatus({ ...item, images: item.images.map((image) => image.taskId && taskIds.has(image.taskId) ? { ...image, status: "canceled" as const, taskStatus: undefined, progress: undefined, error: "任务已中止" } : image) }), images: item.images.map((image) => image.taskId && taskIds.has(image.taskId) ? { ...image, status: "canceled" as const, taskStatus: undefined, progress: undefined, error: "任务已中止" } : image) }) };
    });
    await Promise.allSettled(loadingIds.map((id) => cancelImageTask(id)));
    toast.info(`已中止 ${loadingIds.length} 个生成任务`);
  }

  async function continueTimeoutRetry() {
    const retry = timeoutRetry.value;
    if (!retry) return;
    try {
      await resumeImagePoll(retry.taskId, imageTimeoutRetrySecs.value);
      await updateConversation(retry.conversationId, (current) => {
        if (!current) return current!;
        return { ...current, updatedAt: new Date().toISOString(), turns: current.turns.map((turn) => ({ ...turn, status: turn.images.some((image) => image.taskId === retry.taskId) ? "generating" as const : turn.status, error: undefined, images: turn.images.map((image) => image.taskId === retry.taskId ? { ...image, status: "loading" as const, error: undefined, taskStatus: "running" as const, startTime: image.startTime || Date.now() } : image) })) };
      });
      timeoutRetry.value = null;
      toast.info(`已继续等待 ${imageTimeoutRetrySecs.value} 秒`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "续轮询失败"); timeoutRetry.value = null; }
  }
  async function cancelTimeoutRetry() {
    const retry = timeoutRetry.value;
    if (!retry) return;
    await updateConversation(retry.conversationId, (current) => {
      if (!current) return current!;
      return { ...current, updatedAt: new Date().toISOString(), turns: current.turns.map((turn) => { const images = turn.images.map((image) => image.taskId === retry.taskId ? { ...image, status: "error" as const, error: retry.taskError } : image); return { ...turn, ...deriveTurnStatus({ ...turn, images }), images }; }) };
    });
    timeoutRetry.value = null;
    toast.error(retry.taskError);
  }
  async function dismissErrors(turnId: string) {
    const conversation = selectedConversation.value;
    if (!conversation) return;
    await updateConversation(conversation.id, (current) => {
      if (!current) return current!;
      return { ...current, updatedAt: new Date().toISOString(), turns: current.turns.map((turn) => { if (turn.id !== turnId) return turn; const images = turn.images.filter((image) => image.status !== "error"); return { ...turn, count: images.length, ...deriveTurnStatus({ ...turn, images }), images }; }) };
    });
  }
  function requestDeletePrompt(turnId: string) { if (selectedConversation.value) deleteConfirm.value = { type: "prompt", conversationId: selectedConversation.value.id, turnId }; }
  function requestDeleteResults(turnId: string) { if (selectedConversation.value) deleteConfirm.value = { type: "results", conversationId: selectedConversation.value.id, turnId }; }
  function requestDeleteConversation(id: string) { deleteConfirm.value = { type: "one", id }; }
  function requestClearHistory() { deleteConfirm.value = { type: "all" }; }
  async function deleteConversation(id: string) {
    conversations.value = conversations.value.filter((item) => item.id !== id);
    if (selectedConversationId.value === id) setSelectedConversationId(null, false);
    await deleteImageConversation(id);
  }
  async function deleteTurnPart(conversationId: string, turnId: string, part: "prompt" | "results") {
    const conversation = conversations.value.find((item) => item.id === conversationId);
    if (!conversation) return;
    const turns = conversation.turns.map((turn) => {
      if (turn.id !== turnId) return turn;
      const images = part === "results" ? turn.images.map((image) => ({ id: image.id, status: "error" as const, error: "生成结果已删除" })) : turn.images;
      return { ...turn, prompt: part === "prompt" ? "" : turn.prompt, promptDeleted: part === "prompt" ? true : turn.promptDeleted, resultsDeleted: part === "results" ? true : turn.resultsDeleted, images, ...deriveTurnStatus({ ...turn, images }) };
    }).filter((turn) => !(turn.promptDeleted && turn.resultsDeleted));
    if (!turns.length) return deleteConversation(conversationId);
    await persistConversation({ ...conversation, turns, updatedAt: new Date().toISOString() });
  }
  async function confirmDelete() {
    const target = deleteConfirm.value;
    deleteConfirm.value = null;
    if (!target) return;
    if (target.type === "all") { await clearImageConversations(); conversations.value = []; setSelectedConversationId(null, false); clearComposer(); toast.success("已清空历史记录"); return; }
    if (target.type === "one") { await deleteConversation(target.id); return; }
    await deleteTurnPart(target.conversationId, target.turnId, target.type);
  }
  async function renameConversation(id: string, title: string) { await renameImageConversation(id, title); conversations.value = conversations.value.map((item) => item.id === id ? { ...item, title, updatedAt: new Date().toISOString() } : item); }
  async function reuseTurnConfig(turnId: string) {
    const turn = selectedConversation.value?.turns.find((item) => item.id === turnId);
    if (!turn || !turn.prompt.trim()) return;
    imagePrompt.value = turn.prompt; imageCount.value = String(Math.max(1, turn.count || turn.images.length || 1)); imageRatio.value = turn.ratio; imageTier.value = turn.tier; const parsed = parseImageSize(turn.size); imageWidth.value = parsed.width; imageHeight.value = parsed.height; imageQuality.value = turn.quality; imageModel.value = resolveAllowedImageModel(turn.model); selectedTemplateId.value = turn.templateId || null; preserveSubject.value = turn.preserveSubject === true; referenceImages.value = turn.referenceImages; toast.success("已复用这条提示词配置"); await nextTick();
  }
  async function continueEdit(image: StoredImage | StoredReferenceImage) {
    try {
      const reference = "dataUrl" in image ? { referenceImage: image, file: dataUrlToFile(image.dataUrl, image.name, image.type) } : await storedImageToReference(image, `conversation-${Date.now()}.png`);
      if (!reference) return;
      referenceImages.value = [...referenceImages.value, reference.referenceImage];
      imagePrompt.value = "";
      toast.success("已加入当前参考图，继续输入描述即可编辑");
    } catch (error) { toast.error(error instanceof Error ? error.message : "读取结果图失败"); }
  }
  function openLightbox(images: Array<{ id: string; src: string; name?: string }>, index: number) { lightboxImages.value = images; lightboxIndex.value = Math.max(0, index); lightboxOpen.value = true; }

  function persistPreferences() {
    localStorage.setItem(IMAGE_RATIO_STORAGE_KEY, imageRatio.value);
    localStorage.setItem(IMAGE_TIER_STORAGE_KEY, imageTier.value);
    localStorage.setItem(IMAGE_QUALITY_STORAGE_KEY, imageQuality.value);
    localStorage.setItem(IMAGE_MODEL_STORAGE_KEY, imageModel.value);
    if (parsedCount.value > 0) localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, String(parsedCount.value));
    localStorage.setItem(PRESERVE_SUBJECT_STORAGE_KEY, preserveSubject.value ? "true" : "false");
  }

  onMounted(async () => {
    unmounted = false;
    await Promise.all([loadInitialData(), loadHistory()]);
    scanQueues();
  });
  onBeforeUnmount(() => { unmounted = true; activeImageTurnQueueIds.clear(); submittedImageTaskIds.clear(); });
  watch([imageRatio, imageTier, imageQuality, imageModel, imageCount, preserveSubject], persistPreferences);
  watch(selectedConversationId, (id) => {
    if (id) localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, id);
    else localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
  });
  watch(conversations, scanQueues, { deep: false });

  return {
    settingsConfig, imagePrompt, imageCount, imageRatio, imageTier, imageWidth, imageHeight, imageQuality, imageModel, imageModels, promptTemplates, selectedTemplateId, referenceImages, batchProductImage, batchFolderImages, preserveSubject, conversations, selectedConversationId, appendToSelectedConversation, isSubmitting, isLoadingHistory, availableQuota, historyOpen, deleteConfirm, timeoutRetry, lightboxOpen, lightboxIndex, lightboxImages, isOpenAIRelayEnabled, imageTimeoutRetrySecs, parsedCount, selectedConversation, activeTaskCount, todayGeneratedCount, totalGeneratedCount, displayModel, deleteConfirmTitle, deleteConfirmDescription, formatConversationTime, createDraft, selectConversation, submit, appendReferenceFiles, removeReference, pickBatchProduct, pickBatchFolder, clearBatch, requestDeletePrompt, requestDeleteResults, requestDeleteConversation, requestClearHistory, confirmDelete, renameConversation, regenerateTurn, retryImage, cancelTurn, continueTimeoutRetry, cancelTimeoutRetry, dismissErrors, reuseTurnConfig, continueEdit, openLightbox,
  };
}

