"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, History, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ImageComposer } from "@/app/image/components/image-composer";
import { ImageResults, type ImageLightboxItem } from "@/app/image/components/image-results";
import { ImageSidebar } from "@/app/image/components/image-sidebar";
import { ImageLightbox } from "@/components/image-lightbox";
import { UltraScrollNavigator } from "@/components/ultra-scroll-navigator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  createImageEditTask,
  createImageGenerationTask,
  fetchModels,
  fetchImageTasks,
  fetchProducts,
  fetchPromptTemplates,
  fetchSettingsConfig,
  reportImageFailure,
  resumeImagePoll,
  type BusinessProduct,
  type ImageModel,
  type Model,
  type ImageTask,
  type PromptTemplate,
  type SettingsConfig,
  cancelImageTask,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuthGuard } from "@/lib/use-auth-guard";
import {
  clearImageConversations,
  deleteImageConversation,
  getImageConversationStats,
  listImageConversations,
  renameImageConversation,
  saveImageConversation,
  saveImageConversations,
  type ImageConversation,
  type ImageConversationMode,
  type ImageBatchReplacePlan,
  type ImageTurn,
  type ImageTurnStatus,
  type StoredImage,
  type StoredReferenceImage,
} from "@/store/image-conversations";

const ACTIVE_CONVERSATION_STORAGE_KEY = "gmkraw:image_single_active_conversation_id";
const IMAGE_RATIO_STORAGE_KEY = "gmkraw:image_last_ratio";
const IMAGE_TIER_STORAGE_KEY = "gmkraw:image_last_tier";
const IMAGE_QUALITY_STORAGE_KEY = "gmkraw:image_last_quality";
const IMAGE_MODEL_STORAGE_KEY = "gmkraw:image_last_model";
const PRESERVE_SUBJECT_STORAGE_KEY = "gmkraw:image_preserve_subject";
const DEFAULT_IMAGE_COUNT = "1";
const BUILTIN_IMAGE_MODELS: ImageModel[] = [
  "gpt-image-2",
  "gemini-3.1-flash-image-preview",
  "gpt-image-2-guan",
];
const IMAGE_COUNT_STORAGE_KEY = "gmkraw:image_last_count";
const IMAGE_COUNT_DEFAULT_MIGRATION_KEY = "gmkraw:image_count_default_one_applied";
const SCROLL_POSITIONS_STORAGE_KEY = "gmkraw:image_scroll_positions";
const SCROLL_TO_LATEST_THRESHOLD = 160;
const BATCH_REPLACE_BASE_PROMPT = [
  "以第一张参考图作为唯一商品主体，逐张处理第二张参考图。",
  "把第二张参考图中的原商品替换为第一张参考图里的商品。",
  "保持第二张参考图的背景、构图、光线、透视、人物、道具、版式和画幅不变。",
  "保持第一张商品的包装结构、Logo、可见文字、颜色、材质和比例一致。",
  "只替换商品，不改变场景中的其他元素，不新增无关文案。",
].join("\n");

const imageFileNamePattern = /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i;

function loadScrollPositions(): Map<string, number> {
  if (typeof window === "undefined") return new Map();
  try {
    const raw = window.sessionStorage.getItem(SCROLL_POSITIONS_STORAGE_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as Record<string, number>;
    return new Map(Object.entries(parsed));
  } catch {
    return new Map();
  }
}

function saveScrollPositions(positions: Map<string, number>) {
  if (typeof window === "undefined") return;
  try {
    const obj: Record<string, number> = {};
    positions.forEach((value, key) => { obj[key] = value; });
    window.sessionStorage.setItem(SCROLL_POSITIONS_STORAGE_KEY, JSON.stringify(obj));
  } catch {
    // sessionStorage may be full or unavailable
  }
}

function clampImageCount(value: string) {
  return String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}
function parseImageSize(size: string) {
  const match = size.match(/^(\d+)x(\d+)$/);
  return match ? { width: match[1], height: match[2] } : { width: "1024", height: "1024" };
}

const activeImageTurnQueueIds = new Set<string>();
const canceledImageTaskIds = new Set<string>();
let pollAbortController: AbortController | null = null;
const reportedFailureTaskIds = new Set<string>();

type VisibleFailureReport = {
  taskId: string;
  error?: string;
  mode?: ImageConversationMode;
  model?: ImageModel;
  productId?: number;
  templateId?: number;
};

function imageTurnQueueKey(conversationId: string, turnId: string) {
  return `${conversationId}:${turnId}`;
}

function shouldRunImageTurn(turn: ImageTurn) {
  return (
    !turn.resultsDeleted &&
    (turn.status === "queued" || turn.status === "generating") &&
    turn.images.some((image) => image.status === "loading")
  );
}

async function reportVisibleImageFailures(reports: VisibleFailureReport[]) {
  const uniqueReports = reports.filter((report) => {
    const taskId = String(report.taskId || "").trim();
    if (!taskId || reportedFailureTaskIds.has(taskId)) {
      return false;
    }
    reportedFailureTaskIds.add(taskId);
    return true;
  });
  if (uniqueReports.length === 0) {
    return;
  }
  await Promise.allSettled(
    uniqueReports.map((report) =>
      reportImageFailure({
        taskId: report.taskId,
        error: report.error,
        imageCount: 1,
        mode: report.mode,
        model: report.model,
        productId: report.productId,
        templateId: report.templateId,
      }),
    ),
  );
}

function getResultsDistanceFromBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight;
}

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  if (trimmed.length <= 12) {
    return trimmed;
  }
  return `${trimmed.slice(0, 12)}...`;
}

function buildContextPrompt(previousPrompt: string, currentPrompt: string) {
  const previous = previousPrompt.trim();
  const current = currentPrompt.trim();
  if (!previous) {
    return current;
  }
  return [
    `上一轮需求：${previous}`,
    `当前继续要求：${current}`,
    "请基于参考图继续生成，保持同一商品主体、Logo、文字和核心外观一致，只按当前要求调整画面。",
  ].join("\n");
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatAvailableQuota(_accounts: never[]) {
  void _accounts;
  return "API";
}

function createId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}

function isImageUploadFile(file: File) {
  return file.type.startsWith("image/") || (!file.type && imageFileNamePattern.test(file.name));
}

async function fileToStoredReferenceImage(file: File): Promise<StoredReferenceImage> {
  return {
    name: file.name,
    type: file.type || "image/png",
    dataUrl: await readFileAsDataUrl(file),
  };
}

function pickImageFiles(options: { directory?: boolean; multiple?: boolean }) {
  return new Promise<File[]>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = options.multiple !== false;
    if (options.directory) {
      input.setAttribute("webkitdirectory", "");
      input.setAttribute("directory", "");
    }
    input.style.position = "fixed";
    input.style.left = "-9999px";
    input.style.top = "-9999px";
    input.addEventListener(
      "change",
      () => {
        const files = Array.from(input.files || []);
        input.remove();
        resolve(files);
      },
      { once: true },
    );
    document.body.appendChild(input);
    input.click();
  });
}

function buildBatchReplacePrompt(userPrompt: string) {
  const trimmed = userPrompt.trim();
  if (!trimmed) {
    return BATCH_REPLACE_BASE_PROMPT;
  }
  return `${BATCH_REPLACE_BASE_PROMPT}\n\n用户补充要求：\n${trimmed}`;
}

function dataUrlToFile(dataUrl: string, fileName: string, mimeType?: string) {
  const [header, content] = dataUrl.split(",", 2);
  const matchedMimeType = header.match(/data:(.*?);base64/)?.[1];
  const binary = atob(content || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new File([bytes], fileName, { type: mimeType || matchedMimeType || "image/png" });
}

function filterImageModels(items: Model[]): ImageModel[] {
  const relayModels = items
    .map((item) => String(item.id || "").trim())
    .filter((id) => {
      const lower = id.toLowerCase();
      return lower.includes("image") || lower.includes("banana") || id === "gemini-3.1-flash-image-preview";
    });
  return Array.from(new Set([...BUILTIN_IMAGE_MODELS, ...relayModels]));
}

function normalizeStoredImageModel(value: string | null, availableModels: ImageModel[]): ImageModel {
  const normalized = String(value || "").trim();
  if (normalized && availableModels.includes(normalized)) {
    return normalized;
  }
  return availableModels[0] || "gpt-image-2";
}

function buildReferenceImageFromResult(image: StoredImage, fileName: string): StoredReferenceImage | null {
  if (!image.b64_json) {
    return null;
  }

  return {
    name: fileName,
    type: "image/png",
    dataUrl: `data:image/png;base64,${image.b64_json}`,
  };
}

function isPrivateReferenceHost(hostname: string) {
  const host = hostname.trim().toLowerCase();
  if (!host) return false;
  if (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host === "[::1]" ||
    host.endsWith(".local")
  ) {
    return true;
  }
  const parts = host.split(".").map((part) => Number(part));
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }
  const [first, second] = parts;
  return (
    first === 10 ||
    first === 127 ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 168) ||
    (first === 169 && second === 254)
  );
}

function isPublicReferenceUrl(rawUrl: string) {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return false;
    }
    return !isPrivateReferenceHost(parsed.hostname);
  } catch {
    return false;
  }
}

async function fetchImageAsFile(url: string, fileName: string) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("读取结果图失败");
  }
  const blob = await response.blob();
  return new File([blob], fileName, { type: blob.type || "image/png" });
}

async function buildReferenceImageFromStoredImage(image: StoredImage, fileName: string) {
  const direct = buildReferenceImageFromResult(image, fileName);
  if (direct) {
    return {
      referenceImage: direct,
      file: dataUrlToFile(direct.dataUrl, direct.name, direct.type),
    };
  }

  if (!image.url) {
    return null;
  }
  const file = await fetchImageAsFile(image.url, fileName);
  return {
    referenceImage: {
      name: file.name,
      type: file.type || "image/png",
      dataUrl: await readFileAsDataUrl(file),
      url: image.url,
    },
    file,
  };
}

function buildReferencePayload(referenceImages: StoredReferenceImage[], turnId: string) {
  const files: File[] = [];
  const urls: string[] = [];
  referenceImages.forEach((image, index) => {
    const url = String(image.url || "").trim();
    if (url && isPublicReferenceUrl(url)) {
      urls.push(url);
      return;
    }
    if (image.dataUrl) {
      files.push(dataUrlToFile(image.dataUrl, image.name || `${turnId}-${index + 1}.png`, image.type));
    }
  });
  return { files, urls };
}

async function buildReferencePayloadWithFiles(referenceImages: StoredReferenceImage[], turnId: string) {
  const files: File[] = [];
  const urls: string[] = [];
  for (const [index, image] of referenceImages.entries()) {
    const url = String(image.url || "").trim();
    if (url && isPublicReferenceUrl(url)) {
      urls.push(url);
      continue;
    }
    if (image.dataUrl) {
      files.push(dataUrlToFile(image.dataUrl, image.name || `${turnId}-${index + 1}.png`, image.type));
    } else if (url) {
      const file = await fetchImageAsFile(url, image.name || `${turnId}-${index + 1}.png`);
      files.push(file);
    }
  }
  return { files, urls };
}

async function buildAutoContextFromConversation(conversation: ImageConversation | null, turnId: string) {
  if (!conversation) {
    return null;
  }
  const sourceTurn = [...conversation.turns]
    .reverse()
    .find((turn) => !turn.resultsDeleted && turn.images.some((image) => image.status === "success" && (image.b64_json || image.url)));
  const sourceImage = sourceTurn?.images.find((image) => image.status === "success" && (image.b64_json || image.url));
  if (!sourceTurn || !sourceImage) {
    return null;
  }
  const reference = await buildReferenceImageFromStoredImage(sourceImage, `context-${turnId}.png`);
  if (!reference) {
    return null;
  }
  return {
    referenceImages: [reference.referenceImage],
    referenceImageFiles: [reference.file],
    prompt: sourceTurn.prompt,
  };
}

function taskDataToStoredImage(image: StoredImage, task: ImageTask): StoredImage {
  if (task.status === "success") {
    const first = task.data?.[0];
    if (!first?.b64_json && !first?.url) {
      return {
        ...image,
        taskId: task.id,
        status: "error",
        taskStatus: undefined,
        progress: undefined,
        error: "未返回图片数据",
      };
    }
    return {
      ...image,
      taskId: task.id,
      status: "success",
      taskStatus: undefined,
      progress: undefined,
      b64_json: first.b64_json,
      url: first.url,
      revised_prompt: first.revised_prompt,
      error: undefined,
      durationMs: task.duration_ms,
    };
  }

  if (task.status === "error") {
    return {
      ...image,
      taskId: task.id,
      status: "error",
      taskStatus: undefined,
      progress: undefined,
      error: task.error || "生成失败",
      durationMs: task.duration_ms,
    };
  }

  if (task.status === "canceled") {
    return {
      ...image,
      taskId: task.id,
      status: "canceled",
      taskStatus: undefined,
      progress: undefined,
      error: task.error || "任务已中止",
      durationMs: task.duration_ms,
    };
  }

  const newTaskStatus = task.status === "queued" ? "queued" : task.status === "running" ? "running" : image.taskStatus;
  const shouldSetStartTime = newTaskStatus === "running" && !image.startTime;
  const startTime = shouldSetStartTime ? Date.now() : image.startTime;
  // elapsedSecs 仅使用后端返回的值，确保计时从 image_stream_resolve_start 开始
  const elapsedSecs =
    newTaskStatus === "running" && typeof task.elapsed_secs === "number"
      ? task.elapsed_secs
      : undefined;

  return {
    ...image,
    taskId: task.id,
    status: "loading",
    taskStatus: newTaskStatus,
    progress: task.progress || image.progress,
    error: undefined,
    startTime,
    elapsedSecs,
    elapsedUpdatedAt: elapsedSecs != null ? Date.now() : undefined,
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function pickFallbackConversationId(conversations: ImageConversation[]) {
  const activeConversation = conversations.find((conversation) =>
    conversation.turns.some((turn) => turn.status === "queued" || turn.status === "generating"),
  );
  return activeConversation?.id ?? conversations[0]?.id ?? null;
}

function sortImageConversations(conversations: ImageConversation[]) {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function deriveTurnStatus(turn: ImageTurn): Pick<ImageTurn, "status" | "error"> {
  const loadingCount = turn.images.filter((image) => image.status === "loading").length;
  const failedCount = turn.images.filter((image) => image.status === "error").length;
  const canceledCount = turn.images.filter((image) => image.status === "canceled").length;
  const successCount = turn.images.filter((image) => image.status === "success").length;
  if (loadingCount > 0) {
    // 如果任何图片的 taskStatus 为 running，则状态为 generating
    const hasRunning = turn.images.some((image) => image.taskStatus === "running");
    if (hasRunning) {
      return { status: "generating", error: undefined };
    }
    return { status: turn.status === "queued" ? "queued" : "generating", error: undefined };
  }
  if (failedCount > 0) {
    return { status: "error", error: `其中 ${failedCount} 张未成功生成` };
  }
  if (canceledCount > 0) {
    return { status: "canceled", error: undefined };
  }
  if (successCount > 0) {
    return { status: "success", error: undefined };
  }
  // 所有图片都被忽略（images 为空），视为完成
  return { status: "success", error: undefined };
}

function finalizeIdleQueuedTurn(turn: ImageTurn): ImageTurn {
  if (
    (turn.status !== "queued" && turn.status !== "generating") ||
    turn.images.some((image) => image.status === "loading")
  ) {
    return turn;
  }
  const derived = deriveTurnStatus(turn);
  if (derived.status === turn.status && derived.error === turn.error) {
    return turn;
  }
  return {
    ...turn,
    ...derived,
  };
}

async function syncConversationImageTasks(items: ImageConversation[]) {
  const taskIds = Array.from(
    new Set(
      items.flatMap((conversation) =>
        conversation.turns.flatMap((turn) =>
          turn.resultsDeleted
            ? []
            : turn.images.flatMap((image) =>
                (image.status === "loading" || (image.status === "error" && image.taskId))
                  ? [image.taskId!]
                  : [],
              ),
        ),
      ),
    ),
  );
  if (taskIds.length === 0) {
    return items;
  }

  let taskList: Awaited<ReturnType<typeof fetchImageTasks>>;
  try {
    taskList = await fetchImageTasks(taskIds);
  } catch {
    return items;
  }
  const taskMap = new Map(taskList.items.map((task) => [task.id, task]));
  let changed = false;
  const failureReports: VisibleFailureReport[] = [];
  const normalized = items.map((conversation) => {
    const turns = conversation.turns.map((turn) => {
      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (!image.taskId) {
          return image;
        }
        if (image.status !== "loading" && image.status !== "error") {
          return image;
        }
        const task = taskMap.get(image.taskId);
        if (!task) {
          return image;
        }
        const nextImage = taskDataToStoredImage(image, task);
        if (image.status !== "error" && nextImage.status === "error") {
          failureReports.push({
            taskId: task.id,
            error: nextImage.error,
            mode: turn.mode,
            model: turn.model,
            productId: turn.productId,
            templateId: turn.templateId,
          });
        }
        if (nextImage !== image) {
          turnChanged = true;
        }
        return nextImage;
      });
      if (!turnChanged) {
        return turn;
      }
      changed = true;
      const derived = deriveTurnStatus({ ...turn, images });
      return {
        ...turn,
        ...derived,
        images,
      };
    });
    if (turns === conversation.turns || !turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }
    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }
  await reportVisibleImageFailures(failureReports);
  return normalized;
}

async function recoverConversationHistory(items: ImageConversation[]) {
  let changed = false;
  const normalized = items.map((conversation) => {
    const turns = conversation.turns.map((turn) => {
      if (turn.status !== "queued" && turn.status !== "generating" && turn.status !== "error") {
        return turn;
      }

      let turnChanged = false;
      const images = turn.images.map((image) => {
        if (image.status !== "loading" || image.taskId) {
          return image;
        }
        turnChanged = true;
        return {
          ...image,
          status: "error" as const,
          error: "页面刷新或任务中断，未找到可恢复的任务 ID",
        };
      });
      const candidateTurn = turnChanged ? { ...turn, images } : turn;
      const nextTurn = finalizeIdleQueuedTurn(candidateTurn);
      const derived = turnChanged ? deriveTurnStatus(nextTurn) : { status: nextTurn.status, error: nextTurn.error };
      if (!turnChanged && nextTurn === turn && derived.status === turn.status && derived.error === turn.error) {
        return turn;
      }
      changed = true;
      return {
        ...nextTurn,
        ...derived,
      };
    });

    if (!turns.some((turn, index) => turn !== conversation.turns[index])) {
      return conversation;
    }

    return {
      ...conversation,
      turns,
      updatedAt: new Date().toISOString(),
    };
  });

  if (changed) {
    await saveImageConversations(normalized);
  }

  return syncConversationImageTasks(normalized);
}

function SingleRecentStrip({
  conversations,
  selectedConversationId,
  onSelectConversation,
  formatConversationTime,
}: {
  conversations: ImageConversation[];
  selectedConversationId: string | null;
  onSelectConversation: (id: string) => void;
  formatConversationTime: (value: string) => string;
}) {
  const recent = conversations.slice(0, 4);
  if (recent.length === 0) {
    return null;
  }

  return (
    <div className="rounded-[20px] border border-black/[0.06] bg-white px-4 py-3 dark:border-white/10 dark:bg-[#171a21]">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold text-slate-950 dark:text-stone-50">最近图片任务</h2>
          <p className="mt-0.5 text-[12px] text-slate-500 dark:text-stone-400">快速回到刚才的单张创作上下文。</p>
        </div>
        <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-500 dark:bg-white/[0.08]">
          {conversations.length} 项
        </span>
      </div>
      <div className="hide-scrollbar flex gap-3 overflow-x-auto pb-1">
        {recent.map((conversation) => {
          const active = conversation.id === selectedConversationId;
          const stats = getImageConversationStats(conversation);
          const successCount = conversation.turns.reduce(
            (total, turn) => total + turn.images.filter((image) => image.status === "success").length,
            0,
          );
          return (
            <button
              key={conversation.id}
              type="button"
              onClick={() => onSelectConversation(conversation.id)}
              className={cn(
                "min-w-[220px] rounded-2xl border px-4 py-3 text-left transition-colors duration-200",
                active
                  ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]"
                  : "border-black/[0.06] bg-[#F8FAFC] text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200",
              )}
            >
              <span className="block truncate text-[14px] font-semibold">{conversation.title}</span>
              <span className="mt-1 block text-[12px] text-slate-500">
                {conversation.turns.length} 轮 / {formatConversationTime(conversation.updatedAt)}
              </span>
              <span className="mt-2 inline-flex rounded-full bg-white px-2 py-1 text-[11px] text-slate-500 dark:bg-white/[0.08]">
                {stats.running > 0 ? `${stats.running} 个生成中` : `${successCount} 张成功`}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ImagePageContent({
  isAdmin,
}: {
  isAdmin: boolean;
}) {
  const didLoadQuotaRef = useRef(false);
  const conversationsRef = useRef<ImageConversation[]>([]);
  const loadCancelledRef = useRef(false);
  const resultsViewportRef = useRef<HTMLDivElement>(null);
  const lastConversationIdRef = useRef<string | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollRafRef = useRef<number | null>(null);
  const scrollSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollPositionsRef = useRef<Map<string, number>>(loadScrollPositions());
  const isRestoringScrollRef = useRef(false);
  const scrollRestoreGenerationRef = useRef(0);

  const [settingsConfig, setSettingsConfig] = useState<SettingsConfig | null>(null);
  const isOpenAIRelayEnabled = Boolean(
    settingsConfig?.openai_relay?.enabled
    && settingsConfig.openai_relay.base_url
    && settingsConfig.openai_relay.api_key,
  );
  const imageTimeoutRetrySecs = Number(settingsConfig?.image_timeout_retry_secs || 30);

  const [imagePrompt, setImagePrompt] = useState("");
  const [imageCount, setImageCount] = useState(DEFAULT_IMAGE_COUNT);
  const [imageRatio, setImageRatio] = useState("auto");
  const [imageTier, setImageTier] = useState("1k");
  const [imageWidth, setImageWidth] = useState("1024");
  const [imageHeight, setImageHeight] = useState("1024");
  const [imageQuality, setImageQuality] = useState("auto");
  const [imageModel, setImageModel] = useState<ImageModel>("gpt-image-2");
  const [imageModels, setImageModels] = useState<ImageModel[]>(BUILTIN_IMAGE_MODELS);
  const [products, setProducts] = useState<BusinessProduct[]>([]);
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplate[]>([]);
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [referenceImageFiles, setReferenceImageFiles] = useState<File[]>([]);
  const [referenceImages, setReferenceImages] = useState<StoredReferenceImage[]>([]);
  const [batchProductImage, setBatchProductImage] = useState<StoredReferenceImage | null>(null);
  const [batchFolderImages, setBatchFolderImages] = useState<StoredReferenceImage[]>([]);
  const [preserveSubject, setPreserveSubject] = useState(false);
  const [conversations, setConversations] = useState<ImageConversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [availableQuota, setAvailableQuota] = useState("加载中...");
  const [lightboxImages, setLightboxImages] = useState<ImageLightboxItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const scrollToLatestBtnRef = useRef<HTMLButtonElement>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<
    | { type: "one"; id: string }
    | { type: "prompt"; conversationId: string; turnId: string }
    | { type: "results"; conversationId: string; turnId: string }
    | { type: "all" }
    | null
  >(null);
  const [timeoutRetry, setTimeoutRetry] = useState<{
    conversationId: string;
    taskId: string;
    taskError: string;
  } | null>(null);

  const parsedCount = useMemo(() => Number(clampImageCount(imageCount)), [imageCount]);
  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );
  const activeTaskCount = useMemo(
    () =>
      conversations.reduce((sum, conversation) => {
        const stats = getImageConversationStats(conversation);
        return sum + stats.queued + stats.running;
      }, 0),
    [conversations],
  );
  const deleteConfirmTitle =
    deleteConfirm?.type === "all"
      ? "清空历史记录"
      : deleteConfirm?.type === "prompt"
        ? "删除提示词记录"
        : deleteConfirm?.type === "results"
          ? "删除生成结果"
          : deleteConfirm?.type === "one"
            ? "删除对话"
            : "";
  const deleteConfirmDescription =
    deleteConfirm?.type === "all"
      ? "确认删除全部图片历史记录吗？删除后无法恢复。"
      : deleteConfirm?.type === "prompt"
        ? "确认删除这条提示词记录吗？对应生成结果会保留。"
        : deleteConfirm?.type === "results"
          ? "确认删除这条生成结果吗？对应提示词记录会保留。"
          : deleteConfirm?.type === "one"
            ? "确认删除这条图片对话吗？删除后无法恢复。"
            : "";

  const handleTemplateChange = useCallback((templateId: number | null) => {
    setSelectedTemplateId(templateId);
    const template = promptTemplates.find((item) => item.id === templateId);
    if (!template) {
      return;
    }
    setImagePrompt(template.content);
    if (template.model) {
      setImageModel(template.model);
    }
    if (template.quality) {
      setImageQuality(template.quality);
    }
    if (template.size) {
      const parsed = parseImageSize(template.size);
      setImageWidth(parsed.width);
      setImageHeight(parsed.height);
      setImageRatio("auto");
      setImageTier("auto");
    }
    setPreserveSubject(template.preserve_subject);
    textareaRef.current?.focus();
  }, [promptTemplates]);

  useEffect(() => {
    let cancelled = false;

    const loadSettings = async () => {
      try {
        const data = await fetchSettingsConfig();
        if (!cancelled) {
          setSettingsConfig(data.config);
        }
      } catch {
        if (!cancelled) {
          setSettingsConfig(null);
        }
      }
    };

    void loadSettings();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadBusinessData = async () => {
      try {
        const [productData, templateData] = await Promise.all([
          fetchProducts({ status: "active" }),
          fetchPromptTemplates(),
        ]);
        if (!cancelled) {
          setProducts(productData.items);
          setPromptTemplates(templateData.items);
        }
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "读取商品和模板失败";
          toast.error(message);
        }
      }
    };

    void loadBusinessData();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  const scrollResultsToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    const element = resultsViewportRef.current;
    if (!element) {
      return;
    }

    shouldStickToBottomRef.current = true;
    const btn = scrollToLatestBtnRef.current;
    if (btn) btn.style.display = "none";
    element.scrollTo({
      top: element.scrollHeight,
      behavior,
    });
  }, []);

  const handleResultsScroll = useCallback(() => {
    if (scrollRafRef.current !== null) {
      return;
    }

    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = null;
      const element = resultsViewportRef.current;
      if (!element) {
        return;
      }

      // 恢复滚动位置期间不处理滚动事件
      if (isRestoringScrollRef.current) {
        return;
      }

      // 保存当前会话的滚动位置（debounce 300ms 写入 sessionStorage）
      const convId = lastConversationIdRef.current;
      if (convId) {
        scrollPositionsRef.current.set(convId, element.scrollTop);
        if (scrollSaveTimerRef.current) clearTimeout(scrollSaveTimerRef.current);
        scrollSaveTimerRef.current = setTimeout(() => {
          scrollSaveTimerRef.current = null;
          saveScrollPositions(scrollPositionsRef.current);
        }, 300);
      }

      const isAwayFromLatest = getResultsDistanceFromBottom(element) > SCROLL_TO_LATEST_THRESHOLD;
      shouldStickToBottomRef.current = !isAwayFromLatest;
      // 直接操作 DOM 控制按钮显隐，避免 setState 触发全组件重渲染
      const btn = scrollToLatestBtnRef.current;
      if (btn) {
        if (isAwayFromLatest) {
          btn.style.display = "";
        } else {
          btn.style.display = "none";
        }
      }
    });
  }, []);

  useEffect(() => {
    return () => {
      if (scrollRafRef.current !== null) {
        window.cancelAnimationFrame(scrollRafRef.current);
      }
      if (scrollSaveTimerRef.current !== null) {
        clearTimeout(scrollSaveTimerRef.current);
        saveScrollPositions(scrollPositionsRef.current);
      }
    };
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const storedRatio =
        typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_RATIO_STORAGE_KEY) : null;
      const storedTier =
        typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_TIER_STORAGE_KEY) : null;
      const storedQuality =
        typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_QUALITY_STORAGE_KEY) : null;
      let storedCount =
        typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_COUNT_STORAGE_KEY) : null;
      if (
        typeof window !== "undefined" &&
        storedCount === "3" &&
        window.localStorage.getItem(IMAGE_COUNT_DEFAULT_MIGRATION_KEY) !== "true"
      ) {
        storedCount = DEFAULT_IMAGE_COUNT;
        window.localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, DEFAULT_IMAGE_COUNT);
        window.localStorage.setItem(IMAGE_COUNT_DEFAULT_MIGRATION_KEY, "true");
      }
      const storedPreserveSubject =
        typeof window !== "undefined" ? window.localStorage.getItem(PRESERVE_SUBJECT_STORAGE_KEY) : null;
      setImageRatio(storedRatio || "1:1");
      setImageTier(storedTier || "1k");
      setImageWidth("1024");
      setImageHeight("1024");
      setImageQuality(storedQuality || "auto");
      setImageCount(storedCount ? clampImageCount(storedCount) : DEFAULT_IMAGE_COUNT);
      setPreserveSubject(storedPreserveSubject === "true");

      const items = await listImageConversations();
      const normalizedItems = await recoverConversationHistory(items);
      if (loadCancelledRef.current) {
        return;
      }

      conversationsRef.current = normalizedItems;
      setConversations(normalizedItems);
      const storedConversationId =
        typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY) : null;
      const nextSelectedConversationId =
        (storedConversationId && normalizedItems.some((conversation) => conversation.id === storedConversationId)
          ? storedConversationId
          : null) ?? pickFallbackConversationId(normalizedItems);
      setSelectedConversationId(nextSelectedConversationId);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取会话记录失败";
      toast.error(message);
    } finally {
      if (!loadCancelledRef.current) {
        setIsLoadingHistory(false);
      }
    }
  }, [
    setImageRatio,
    setImageTier,
    setImageWidth,
    setImageHeight,
    setImageQuality,
    setImageCount,
    setPreserveSubject,
    setConversations,
    setSelectedConversationId,
    setIsLoadingHistory,
  ]);

  // Handle bfcache (back/forward cache) — re-sync task status on page restore
  useEffect(() => {
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        void loadHistory();
      }
    };
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, [loadHistory]);

  useEffect(() => {
    loadCancelledRef.current = false;
    void loadHistory();
    return () => {
      loadCancelledRef.current = true;
      // 组件卸载时保存当前滚动位置到 sessionStorage
      const element = resultsViewportRef.current;
      const convId = lastConversationIdRef.current;
      if (element && convId) {
        scrollPositionsRef.current.set(convId, element.scrollTop);
        saveScrollPositions(scrollPositionsRef.current);
      }
      activeImageTurnQueueIds.clear();
      if (pollAbortController) {
        pollAbortController.abort();
        pollAbortController = null;
      }
    };
  }, [loadHistory]);

  useEffect(() => {
    let cancelled = false;

    const loadImageModels = async () => {
      try {
        const data = await fetchModels();
        const available = filterImageModels(Array.isArray(data.data) ? data.data : []);
        if (cancelled) {
          return;
        }
        setImageModels(available);
        const storedModel = typeof window !== "undefined" ? window.localStorage.getItem(IMAGE_MODEL_STORAGE_KEY) : null;
        setImageModel((current) => {
          if (available.includes(current)) {
            return current;
          }
          return normalizeStoredImageModel(storedModel, available);
        });
      } catch {
        if (!cancelled) {
          setImageModels(BUILTIN_IMAGE_MODELS);
        }
      }
    };

    void loadImageModels();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadQuota = useCallback(async () => {
    if (!isAdmin) {
      setAvailableQuota("--");
      return;
    }
    setAvailableQuota(isOpenAIRelayEnabled ? "\u4e2d\u8f6c\u7ad9" : "API");
  }, [isAdmin, isOpenAIRelayEnabled]);

  useEffect(() => {
    if (isAdmin && isOpenAIRelayEnabled) {
      setAvailableQuota("\u4e2d\u8f6c\u7ad9");
    }
  }, [isAdmin, isOpenAIRelayEnabled]);

  useEffect(() => {
    if (didLoadQuotaRef.current) {
      return;
    }
    didLoadQuotaRef.current = true;

    const handleFocus = () => {
      void loadQuota();
    };

    void loadQuota();
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [isAdmin, loadQuota]);

  // 切换会话时保存旧会话滚动位置，并隐藏容器防止闪烁
  useLayoutEffect(() => {
    if (!selectedConversation) {
      lastConversationIdRef.current = null;
      shouldStickToBottomRef.current = true;
      const btn = scrollToLatestBtnRef.current;
      if (btn) btn.style.display = "none";
      return;
    }

    const element = resultsViewportRef.current;
    if (!element) {
      return;
    }

    const didSwitchConversation = lastConversationIdRef.current !== selectedConversation.id;

    if (didSwitchConversation) {
      // 递增 generation，使之前未完成的 rAF 回调失效
      scrollRestoreGenerationRef.current += 1;

      // 先保存旧会话的滚动位置（lastConversationIdRef 还是旧值）
      const oldConvId = lastConversationIdRef.current;
      if (oldConvId) {
        scrollPositionsRef.current.set(oldConvId, element.scrollTop);
        saveScrollPositions(scrollPositionsRef.current);
      }
      // 更新为新会话 ID
      lastConversationIdRef.current = selectedConversation.id;

      // 如果有保存的滚动位置，隐藏容器防止用户看到 scrollTop=0 的内容
      const savedScrollTop = scrollPositionsRef.current.get(selectedConversation.id);
      if (savedScrollTop != null && savedScrollTop > 0) {
        element.style.visibility = "hidden";
        isRestoringScrollRef.current = true;
      }
    }
  }, [selectedConversation?.id]);

  // 恢复滚动位置或跟随最新内容
  useEffect(() => {
    if (!selectedConversation) {
      return;
    }

    const element = resultsViewportRef.current;
    if (!element) {
      return;
    }

    const savedScrollTop = scrollPositionsRef.current.get(selectedConversation.id);

    if (savedScrollTop != null && savedScrollTop > 0) {
      // 捕获当前 generation，用于检测是否已被新的切换取代
      const generation = scrollRestoreGenerationRef.current;
      // 容器已在 useLayoutEffect 中设为 visibility:hidden，用户看不到滚动过程
      requestAnimationFrame(() => {
        // 如果 generation 已变，说明用户又切换了，放弃本次恢复
        if (scrollRestoreGenerationRef.current !== generation) return;
        element.scrollTop = savedScrollTop;
        // 再等一帧确保 scrollTop 生效后再显示容器
        requestAnimationFrame(() => {
          // 再次检查 generation
          if (scrollRestoreGenerationRef.current !== generation) return;
          const isAwayFromLatest = getResultsDistanceFromBottom(element) > SCROLL_TO_LATEST_THRESHOLD;
          shouldStickToBottomRef.current = !isAwayFromLatest;
          const btn = scrollToLatestBtnRef.current;
          if (btn) btn.style.display = isAwayFromLatest ? "" : "none";
          // 显示容器 — 用户直接看到正确位置的内容
          element.style.visibility = "";
          isRestoringScrollRef.current = false;
        });
      });
      // 恢复后清除保存的位置，下次内容更新时走正常的 shouldFollowLatest 逻辑
      scrollPositionsRef.current.delete(selectedConversation.id);
      return;
    }

    // 无保存位置，按正常逻辑处理
    const shouldFollowLatest =
      shouldStickToBottomRef.current ||
      getResultsDistanceFromBottom(element) <= SCROLL_TO_LATEST_THRESHOLD;

    if (shouldFollowLatest) {
      requestAnimationFrame(() => scrollResultsToLatest("smooth"));
      return;
    }

    const btn = scrollToLatestBtnRef.current;
    if (btn) btn.style.display = "";
  }, [selectedConversation?.id, selectedConversation?.updatedAt, selectedConversation?.turns.length, scrollResultsToLatest]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    if (selectedConversationId) {
      window.localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, selectedConversationId);
    } else {
      window.localStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    }
  }, [selectedConversationId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(IMAGE_RATIO_STORAGE_KEY, imageRatio);
    window.localStorage.setItem(IMAGE_TIER_STORAGE_KEY, imageTier);
    window.localStorage.setItem(IMAGE_QUALITY_STORAGE_KEY, imageQuality);
    window.localStorage.setItem(IMAGE_MODEL_STORAGE_KEY, imageModel);
  }, [imageRatio, imageTier, imageQuality, imageModel]);

  useEffect(() => {
    if (typeof window !== "undefined" && parsedCount > 0) {
      window.localStorage.setItem(IMAGE_COUNT_STORAGE_KEY, String(parsedCount));
    }
  }, [parsedCount]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(PRESERVE_SUBJECT_STORAGE_KEY, preserveSubject ? "true" : "false");
    }
  }, [preserveSubject]);

  useEffect(() => {
    if (selectedConversationId && !conversations.some((conversation) => conversation.id === selectedConversationId)) {
      setSelectedConversationId(pickFallbackConversationId(conversations));
    }
  }, [conversations, selectedConversationId]);

  const persistConversation = async (conversation: ImageConversation) => {
    const nextConversations = sortImageConversations([
      conversation,
      ...conversationsRef.current.filter((item) => item.id !== conversation.id),
    ]);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    await saveImageConversation(conversation);
  };

  const updateConversation = useCallback(
    async (
      conversationId: string,
      updater: (current: ImageConversation | null) => ImageConversation,
      options: { persist?: boolean } = {},
    ) => {
      const current = conversationsRef.current.find((item) => item.id === conversationId) ?? null;
      const nextConversation = updater(current);
      const nextConversations = sortImageConversations([
        nextConversation,
        ...conversationsRef.current.filter((item) => item.id !== conversationId),
      ]);
      conversationsRef.current = nextConversations;
      setConversations(nextConversations);
      if (options.persist !== false) {
        await saveImageConversation(nextConversation);
      }
    },
    [],
  );

  const clearComposerInputs = useCallback(() => {
    setImagePrompt("");
    setReferenceImageFiles([]);
    setReferenceImages([]);
    setBatchProductImage(null);
    setBatchFolderImages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, []);

  const resetComposer = useCallback(() => {
    clearComposerInputs();
  }, [clearComposerInputs]);

  const handleCreateDraft = () => {
    shouldStickToBottomRef.current = true;
    const btn = scrollToLatestBtnRef.current;
    if (btn) btn.style.display = "none";
    setSelectedConversationId(null);
    resetComposer();
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (id: string) => {
    const nextConversations = conversations.filter((item) => item.id !== id);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    if (selectedConversationId === id) {
      setSelectedConversationId(pickFallbackConversationId(nextConversations));
      resetComposer();
    }

    try {
      await deleteImageConversation(id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const items = await listImageConversations();
      conversationsRef.current = items;
      setConversations(items);
    }
  };

  const handleDeleteTurnPart = async (conversationId: string, turnId: string, part: "prompt" | "results") => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    if (!conversation) {
      return;
    }

    const turns = conversation.turns
      .map((turn) => {
        if (turn.id !== turnId) {
          return turn;
        }
        const images =
          part === "results"
            ? turn.images.map((image) => ({ id: image.id, status: "error" as const, error: "生成结果已删除" }))
            : turn.images;
        const derived =
          part === "results"
            ? deriveTurnStatus({
                ...turn,
                images,
              })
            : { status: turn.status, error: turn.error };
        const nextTurn = {
          ...turn,
          prompt: part === "prompt" ? "" : turn.prompt,
          promptDeleted: part === "prompt" ? true : turn.promptDeleted,
          resultsDeleted: part === "results" ? true : turn.resultsDeleted,
          ...derived,
          images,
        };
        return nextTurn.promptDeleted && nextTurn.resultsDeleted ? null : nextTurn;
      })
      .filter((turn): turn is ImageTurn => Boolean(turn));

    if (turns.length === 0) {
      await handleDeleteConversation(conversationId);
      return;
    }

    const nextConversation = {
      ...conversation,
      updatedAt: new Date().toISOString(),
      turns,
    };
    await persistConversation(nextConversation);
  };

  const handleClearHistory = async () => {
    try {
      await clearImageConversations();
      conversationsRef.current = [];
      setConversations([]);
      setSelectedConversationId(null);
      resetComposer();
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  };

  const handleRenameConversation = async (id: string, title: string) => {
    const nextConversations = conversations.map((item) =>
      item.id === id ? { ...item, title, updatedAt: new Date().toISOString() } : item,
    );
    conversationsRef.current = sortImageConversations(nextConversations);
    setConversations(conversationsRef.current);
    try {
      await renameImageConversation(id, title);
    } catch (error) {
      const message = error instanceof Error ? error.message : "重命名失败";
      toast.error(message);
    }
  };

  const openDeleteConversationConfirm = (id: string) => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "one", id });
  };

  const openDeletePromptConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "prompt", conversationId, turnId });
  };

  const openDeleteResultsConfirm = (conversationId: string, turnId: string) => {
    setDeleteConfirm({ type: "results", conversationId, turnId });
  };

  const openClearHistoryConfirm = () => {
    setIsHistoryOpen(false);
    setDeleteConfirm({ type: "all" });
  };

  const handleConfirmDelete = async () => {
    const target = deleteConfirm;
    setDeleteConfirm(null);
    if (!target) {
      return;
    }
    if (target.type === "all") {
      await handleClearHistory();
      return;
    }
    if (target.type === "prompt" || target.type === "results") {
      await handleDeleteTurnPart(target.conversationId, target.turnId, target.type);
      return;
    }
    await handleDeleteConversation(target.id);
  };

  const appendReferenceImages = useCallback(async (files: File[]) => {
    if (files.length === 0) {
      return;
    }

    try {
      const previews = await Promise.all(
        files.map(async (file) => ({
          name: file.name,
          type: file.type || "image/png",
          dataUrl: await readFileAsDataUrl(file),
        })),
      );

      setReferenceImageFiles((prev) => [...prev, ...files]);
      setReferenceImages((prev) => [...prev, ...previews]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取参考图失败";
      toast.error(message);
    }
  }, []);

  const handleReferenceImageChange = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }

      await appendReferenceImages(files);
    },
    [appendReferenceImages],
  );

  const handlePickBatchProductImage = useCallback(async () => {
    try {
      const files = (await pickImageFiles({ multiple: false })).filter(isImageUploadFile);
      const file = files[0];
      if (!file) {
        return;
      }
      setBatchProductImage(await fileToStoredReferenceImage(file));
      setPreserveSubject(true);
      toast.success("已上传主图，批量替换会以这张图作为商品主体");
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取主图失败";
      toast.error(message);
    }
  }, []);

  const handlePickBatchFolder = useCallback(async () => {
    try {
      const files = (await pickImageFiles({ directory: true, multiple: true })).filter(isImageUploadFile);
      if (files.length === 0) {
        toast.error("文件夹里没有可用图片");
        return;
      }
      const previews = await Promise.all(files.map(fileToStoredReferenceImage));
      setBatchFolderImages(previews);
      setImageCount(String(previews.length));
      setPreserveSubject(true);
      if (!imagePrompt.trim()) {
        setImagePrompt("把主图商品替换到每张文件夹图片中，保持原图场景、光线、构图和风格不变。");
      }
      toast.success(`已读取 ${previews.length} 张文件夹图片`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取文件夹失败";
      toast.error(message);
    }
  }, [imagePrompt]);

  const handleClearBatchReplace = useCallback(() => {
    setBatchProductImage(null);
    setBatchFolderImages([]);
    setImageCount(DEFAULT_IMAGE_COUNT);
    toast.success("已清空批量替换素材");
  }, []);

  const handleRemoveReferenceImage = useCallback((index: number) => {
    setReferenceImageFiles((prev) => {
      const next = prev.filter((_, currentIndex) => currentIndex !== index);
      if (next.length === 0 && fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      return next;
    });
    setReferenceImages((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
  }, []);

  const handleContinueEdit = useCallback(
    async (conversationId: string, image: StoredImage | StoredReferenceImage) => {
      try {
        const nextReference =
          "dataUrl" in image
            ? {
                referenceImage: image,
                file: dataUrlToFile(image.dataUrl, image.name, image.type),
              }
            : await buildReferenceImageFromStoredImage(image, `conversation-${conversationId}-${Date.now()}.png`);
        if (!nextReference) {
          return;
        }

        setSelectedConversationId(conversationId);

        setReferenceImages((prev) => [...prev, nextReference.referenceImage]);
        setReferenceImageFiles((prev) => [...prev, nextReference.file]);
        setImagePrompt("");
        textareaRef.current?.focus();
        toast.success("已加入当前参考图，继续输入描述即可编辑");
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取结果图失败";
        toast.error(message);
      }
    },
    [],
  );

  const handleReuseTurnConfig = useCallback(async (conversationId: string, turnId: string) => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    const turn = conversation?.turns.find((item) => item.id === turnId);
    if (!conversation || !turn || !turn.prompt.trim()) {
      return;
    }

    setSelectedConversationId(conversationId);
    setImagePrompt(turn.prompt);
    setImageCount(String(Math.max(1, turn.count || turn.images.length || 1)));
    setImageRatio(turn.ratio);
    setImageTier(turn.tier);
    const parsedSize = parseImageSize(turn.size);
    setImageWidth(parsedSize.width);
    setImageHeight(parsedSize.height);
    setImageQuality(turn.quality);
    setImageModel(turn.model);
    setSelectedProductId(turn.productId ?? null);
    setSelectedTemplateId(turn.templateId ?? null);
    setPreserveSubject(turn.preserveSubject === true);
    setReferenceImages(turn.referenceImages);
    setReferenceImageFiles(
      turn.referenceImages.map((image) => dataUrlToFile(image.dataUrl, image.name, image.type)),
    );
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    textareaRef.current?.focus();
    toast.success("已复用这条提示词配置");
  }, []);

  const openLightbox = useCallback((images: ImageLightboxItem[], index: number) => {
    if (images.length === 0) {
      return;
    }

    setLightboxImages(images);
    setLightboxIndex(Math.max(0, Math.min(index, images.length - 1)));
    setLightboxOpen(true);
  }, []);

  const createLoadingImages = (turnId: string, count: number) =>
    Array.from({ length: count }, (_, index) => {
      const imageId = `${turnId}-${index}`;
      return {
        id: imageId,
        taskId: imageId,
        status: "loading" as const,
        startTime: Date.now(),
      };
    });

  const createBatchReplaceLoadingImages = (turnId: string, folderImages: StoredReferenceImage[]) =>
    folderImages.map((image, index) => {
      const imageId = `${turnId}-${index}`;
      return {
        id: imageId,
        taskId: imageId,
        status: "loading" as const,
        startTime: Date.now(),
        sourceImageIndex: index,
        sourceName: image.name,
      };
    });

  /* eslint-disable react-hooks/preserve-manual-memoization */
  const runConversationQueue = useCallback(
    async (conversationId: string, preferredTurnId?: string) => {
      const snapshot = conversationsRef.current.find((conversation) => conversation.id === conversationId);
      const activeTurn = snapshot?.turns.find((turn) => {
        if (preferredTurnId && turn.id !== preferredTurnId) {
          return false;
        }
        if (!shouldRunImageTurn(turn)) {
          return false;
        }
        return !activeImageTurnQueueIds.has(imageTurnQueueKey(conversationId, turn.id));
      });
      if (!snapshot || !activeTurn) {
        return;
      }

      const activeQueueKey = imageTurnQueueKey(conversationId, activeTurn.id);
      activeImageTurnQueueIds.add(activeQueueKey);
      const applyTasks = async (tasks: ImageTask[]) => {
        const taskMap = new Map(tasks.map((task) => [task.id, task]));
        const failureReports: VisibleFailureReport[] = [];
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          const turns = conversation.turns.map((turn) => {
            if (turn.id !== activeTurn.id) {
              return turn;
            }
            const images = turn.images.map((image) => {
              const taskId = image.taskId || image.id;
              if (image.status === "canceled" || canceledImageTaskIds.has(taskId)) {
                return image;
              }
              const task = taskMap.get(taskId);
              if (!task) {
                return image;
              }
              const nextImage = taskDataToStoredImage({ ...image, taskId }, task);
              if (image.status !== "error" && nextImage.status === "error") {
                failureReports.push({
                  taskId: task.id,
                  error: nextImage.error,
                  mode: turn.mode,
                  model: turn.model,
                  productId: turn.productId,
                  templateId: turn.templateId,
                });
              }
              return nextImage;
            });
            const derived = deriveTurnStatus({ ...turn, images });
            return {
              ...turn,
              ...derived,
              images,
            };
          });
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns,
          };
        });
        await reportVisibleImageFailures(failureReports);
      };

      try {
        const taskReferenceImages = (image: StoredImage) => {
          const sourceIndex = image.sourceImageIndex;
          const sourceImage =
            activeTurn.batchReplace && typeof sourceIndex === "number"
              ? activeTurn.batchReplace.folderImages[sourceIndex]
              : undefined;
          if (activeTurn.batchReplace && sourceImage) {
            return [activeTurn.batchReplace.productImage, sourceImage];
          }
          return activeTurn.referenceImages;
        };
        const buildTaskReferencePayload = async (image: StoredImage) => {
          const taskId = image.taskId || image.id || activeTurn.id;
          return buildReferencePayloadWithFiles(taskReferenceImages(image), taskId);
        };

        if (activeTurn.mode === "edit" && activeTurn.referenceImages.length === 0 && !activeTurn.batchReplace) {
          throw new Error("未找到可用于继续编辑的参考图");
        }

        const pendingImages = activeTurn.images.filter((image) => image.status === "loading");
        const submitted = (await Promise.all(
          pendingImages.map(async (image) => {
            const taskId = image.taskId || image.id;
            if (canceledImageTaskIds.has(taskId)) {
              return null;
            }
            const referencePayload = await buildTaskReferencePayload(image);
            if (activeTurn.mode === "edit" && referencePayload.files.length === 0 && referencePayload.urls.length === 0) {
              throw new Error("未找到可用于继续编辑的参考图");
            }
            const task = activeTurn.mode === "edit"
              ? createImageEditTask(taskId, referencePayload.files, activeTurn.prompt, activeTurn.model, activeTurn.size, activeTurn.quality, referencePayload.urls, activeTurn.preserveSubject === true, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId)
              : createImageGenerationTask(taskId, activeTurn.prompt, activeTurn.model, activeTurn.size, activeTurn.quality, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId);
            const submittedTask = await task;
            if (canceledImageTaskIds.has(taskId)) {
              await cancelImageTask(taskId).catch(() => undefined);
            }
            return submittedTask;
          }),
        )).filter((task): task is ImageTask => task !== null);
        await applyTasks(submitted);

        let consecutiveErrors = 0;
        const retryingTaskIdsRef = new Set<string>();
        while (true) {
          const latestConversation = conversationsRef.current.find((conversation) => conversation.id === conversationId);
          const latestTurn = latestConversation?.turns.find((turn) => turn.id === activeTurn.id);
          const loadingTaskIds =
            latestTurn?.images.flatMap((image) =>
              image.status === "loading" && image.taskId ? [image.taskId] : [],
            ) || [];
          if (loadingTaskIds.length === 0) {
            break;
          }

          await sleep(2000);
          try {
            const taskList = await fetchImageTasks(loadingTaskIds);
            consecutiveErrors = 0;
            if (taskList.items.length > 0) {
              // 检测是否有超时错误且需要显示重试按钮
              const timeoutTask = !isOpenAIRelayEnabled
                ? taskList.items.find(
                    (task) =>
                      task.status === "error" &&
                      task.error?.includes("超时") &&
                      task.conversation_id &&
                      !retryingTaskIdsRef.has(task.id),
                  )
                : undefined;
              if (timeoutTask && timeoutTask.conversation_id) {
                retryingTaskIdsRef.add(timeoutTask.id);
                setTimeoutRetry({
                  conversationId: timeoutTask.conversation_id,
                  taskId: timeoutTask.id,
                  taskError: timeoutTask.error || "生图超时",
                });
                // 应用超时错误到对应图片，显示继续等待按钮
                await applyTasks([timeoutTask]);
              } else {
                await applyTasks(taskList.items);
              }
            }
            if (taskList.missing_ids.length > 0 && latestTurn) {
              const missingImages = latestTurn.images.filter(
                (image) => image.status === "loading" && image.taskId && taskList.missing_ids.includes(image.taskId),
              );
              const resubmitted = await Promise.all(
                missingImages.map(async (image) => {
                  const taskId = image.taskId || image.id;
                  if (canceledImageTaskIds.has(taskId)) {
                    return null;
                  }
                  const referencePayload = await buildTaskReferencePayload(image);
                  const task = activeTurn.mode === "edit"
                    ? createImageEditTask(taskId, referencePayload.files, activeTurn.prompt, activeTurn.model, activeTurn.size, activeTurn.quality, referencePayload.urls, activeTurn.preserveSubject === true, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId)
                    : createImageGenerationTask(taskId, activeTurn.prompt, activeTurn.model, activeTurn.size, activeTurn.quality, conversationId, activeTurn.id, activeTurn.productId, activeTurn.templateId);
                  const submittedTask = await task;
                  if (canceledImageTaskIds.has(taskId)) {
                    await cancelImageTask(taskId).catch(() => undefined);
                  }
                  return submittedTask;
                }),
              );
              const activeResubmitted = resubmitted.filter((task): task is ImageTask => task !== null);
              if (activeResubmitted.length > 0) {
                await applyTasks(activeResubmitted);
              }
            }
          } catch (pollError) {
            consecutiveErrors += 1;
            if (consecutiveErrors >= 10) {
              throw pollError;
            }
          }
        }

        await loadQuota();
      } catch (error) {
        const message = error instanceof Error ? error.message : "生成图片失败";
        const failureReports: VisibleFailureReport[] = [];
        await updateConversation(conversationId, (current) => {
          const conversation = current ?? snapshot;
          return {
            ...conversation,
            updatedAt: new Date().toISOString(),
            turns: conversation.turns.map((turn) =>
              turn.id === activeTurn.id
                ? {
                    ...turn,
                    status: "error",
                    error: message,
                    images: turn.images.map((image) => {
                      if (image.status !== "loading") {
                        return image;
                      }
                      failureReports.push({
                        taskId: image.taskId || image.id,
                        error: message,
                        mode: turn.mode,
                        model: turn.model,
                        productId: turn.productId,
                        templateId: turn.templateId,
                      });
                      return { ...image, status: "error", error: message };
                    }),
                  }
                : turn,
            ),
          };
        });
        await reportVisibleImageFailures(failureReports);
        toast.error(message);
      } finally {
        activeImageTurnQueueIds.delete(activeQueueKey);
        for (const conversation of conversationsRef.current) {
          for (const turn of conversation.turns) {
            if (shouldRunImageTurn(turn) && !activeImageTurnQueueIds.has(imageTurnQueueKey(conversation.id, turn.id))) {
              void runConversationQueue(conversation.id, turn.id);
            }
          }
        }
      }
    },
    [isOpenAIRelayEnabled, loadQuota, updateConversation],
  );
  /* eslint-enable react-hooks/preserve-manual-memoization */

  const handleRegenerateTurn = useCallback(
    async (conversationId: string, turnId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      const sourceTurn = conversation?.turns.find((turn) => turn.id === turnId);
      if (!conversation || !sourceTurn || !sourceTurn.prompt.trim()) {
        return;
      }

      const now = new Date().toISOString();
      const nextTurnId = createId();
      const count = Math.max(1, sourceTurn.count || sourceTurn.images.length || 1);
      const batchReplace = sourceTurn.batchReplace;
      const nextTurn: ImageTurn = {
        id: nextTurnId,
        prompt: sourceTurn.prompt,
        model: sourceTurn.model,
        mode: sourceTurn.mode,
        referenceImages: sourceTurn.referenceImages,
        batchReplace,
        preserveSubject: sourceTurn.preserveSubject === true,
        count: batchReplace ? batchReplace.folderImages.length : count,
        size: sourceTurn.size,
        ratio: sourceTurn.ratio,
        tier: sourceTurn.tier,
        quality: sourceTurn.quality,
        productId: sourceTurn.productId,
        templateId: sourceTurn.templateId,
        images: batchReplace
          ? createBatchReplaceLoadingImages(nextTurnId, batchReplace.folderImages)
          : createLoadingImages(nextTurnId, count),
        createdAt: now,
        status: "queued",
      };
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: [...conversation.turns, nextTurn],
      };

      setSelectedConversationId(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId, nextTurnId);
      toast.success("已开始重新生成");
    },
    [runConversationQueue],
  );

  const handleRetryImage = useCallback(
    async (conversationId: string, turnId: string, imageId: string) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      if (!conversation) {
        return;
      }

      const now = new Date().toISOString();
      const retryImageId = `${turnId}-${createId()}`;
      const nextConversation = {
        ...conversation,
        updatedAt: now,
        turns: conversation.turns.map((turn) => {
          if (turn.id !== turnId) {
            return turn;
          }
          if (!turn.prompt.trim()) {
            return turn;
          }

          const images = turn.images.map((image) =>
            image.id === imageId
              ? {
                  id: retryImageId,
                  taskId: retryImageId,
                  status: "loading" as const,
                  sourceImageIndex: image.sourceImageIndex,
                  sourceName: image.sourceName,
                }
              : image,
          );
          const derived = deriveTurnStatus({ ...turn, status: "queued", images });
          return {
            ...turn,
            ...derived,
            images,
          };
        }),
      };

      setSelectedConversationId(conversationId);
      await persistConversation(nextConversation);
      void runConversationQueue(conversationId, turnId);
    },
    [runConversationQueue],
  );

  const handleCancelTurn = useCallback(
    async (conversationId: string, turnId: string) => {
      const sourceConversation = conversationsRef.current.find((item) => item.id === conversationId);
      const sourceTurn = sourceConversation?.turns.find((turn) => turn.id === turnId);
      if (!sourceConversation || !sourceTurn) {
        return;
      }

      const loadingTaskIds = sourceTurn.images.flatMap((image) =>
        image.status === "loading" && image.taskId ? [image.taskId] : [],
      );
      if (loadingTaskIds.length === 0) {
        return;
      }

      const taskIdSet = new Set(loadingTaskIds);
      loadingTaskIds.forEach((taskId) => canceledImageTaskIds.add(taskId));
      const now = Date.now();

      await updateConversation(conversationId, (current) => {
        const conversation = current ?? sourceConversation;
        return {
          ...conversation,
          updatedAt: new Date().toISOString(),
          turns: conversation.turns.map((turn) => {
            if (turn.id !== turnId) {
              return turn;
            }
            const images = turn.images.map((image) => {
              const taskId = image.taskId || image.id;
              if (image.status !== "loading" || !taskIdSet.has(taskId)) {
                return image;
              }
              return {
                ...image,
                status: "canceled" as const,
                taskStatus: undefined,
                progress: undefined,
                error: "任务已中止",
                durationMs: image.durationMs ?? (image.startTime ? Math.max(0, now - image.startTime) : undefined),
              };
            });
            const derived = deriveTurnStatus({ ...turn, images });
            return {
              ...turn,
              ...derived,
              images,
            };
          }),
        };
      });

      setTimeoutRetry((current) => (current && taskIdSet.has(current.taskId) ? null : current));
      toast.info(`已中止 ${loadingTaskIds.length} 个生成任务`);
      void Promise.allSettled(loadingTaskIds.map((taskId) => cancelImageTask(taskId)));
    },
    [updateConversation],
  );

  const handleTimeoutRetryContinue = useCallback(async () => {
    if (!timeoutRetry) return;
    const { conversationId, taskId } = timeoutRetry;
    try {
      await resumeImagePoll(taskId, imageTimeoutRetrySecs);
      // 将对应图片的状态重置为 loading，并清除错误
      void updateConversation(conversationId, (current) => {
        const conversation = current ?? conversationsRef.current.find((c) => c.id === conversationId);
        if (!conversation) return current!;
        return {
          ...conversation,
          updatedAt: new Date().toISOString(),
          turns: conversation.turns.map((turn) => {
            const hasLoading = turn.images.some((image) => image.taskId === taskId);
            if (!hasLoading) return turn;
            return {
              ...turn,
              status: "generating" as const,
              error: undefined,
              images: turn.images.map((image) =>
                image.taskId === taskId
                  ? { ...image, status: "loading" as const, error: undefined, taskStatus: "running" as const, startTime: image.startTime || Date.now() }
                  : image
              ),
            };
          }),
        };
      });
      // 清除重试状态
      setTimeoutRetry(null);
      toast.info(`已继续等待 ${imageTimeoutRetrySecs} 秒`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "续轮询失败";
      toast.error(msg);
      setTimeoutRetry(null);
    }
  }, [timeoutRetry, updateConversation, imageTimeoutRetrySecs]);

  const handleTimeoutRetryCancel = useCallback(() => {
    if (!timeoutRetry) return;
    const { conversationId: convId, taskId, taskError } = timeoutRetry;
    // 将超时错误应用到对应图片
    void updateConversation(convId, (current) => {
      const conversation = current ?? conversationsRef.current.find((c) => c.id === convId);
      if (!conversation) return current!;
      return {
        ...conversation,
        updatedAt: new Date().toISOString(),
        turns: conversation.turns.map((turn) => {
          const hasLoading = turn.images.some((image) => image.status === "loading" && image.taskId === taskId);
          if (!hasLoading) return turn;
          const images = turn.images.map((image) =>
            image.taskId === taskId ? { ...image, status: "error" as const, error: taskError } : image,
          );
          const derived = deriveTurnStatus({ ...turn, images });
          return {
            ...turn,
            ...derived,
            images,
          };
        }),
      };
    });
    setTimeoutRetry(null);
    toast.error(taskError);
  }, [timeoutRetry, updateConversation]);

  const handleDismissErrors = useCallback(
    async (conversationId: string, turnId: string) => {
      await updateConversation(conversationId, (current) => {
        const conversation = current ?? conversationsRef.current.find((c) => c.id === conversationId);
        if (!conversation) return current!;
        return {
          ...conversation,
          updatedAt: new Date().toISOString(),
          turns: conversation.turns.map((turn) => {
            if (turn.id !== turnId) return turn;
            const successImages = turn.images.filter((image) => image.status !== "error");
            const derived = deriveTurnStatus({ ...turn, images: successImages });
            return {
              ...turn,
              ...derived,
              count: successImages.length,
              images: successImages,
            };
          }),
        };
      });
    },
    [updateConversation],
  );

  useEffect(() => {
    for (const conversation of conversations) {
      for (const turn of conversation.turns) {
        if (shouldRunImageTurn(turn) && !activeImageTurnQueueIds.has(imageTurnQueueKey(conversation.id, turn.id))) {
          void runConversationQueue(conversation.id, turn.id);
        }
      }
    }
  }, [conversations, runConversationQueue]);

  const handleSubmit = async () => {
    const prompt = imagePrompt.trim();
    const isBatchReplace = Boolean(batchProductImage && batchFolderImages.length > 0);
    if (!prompt && !isBatchReplace) {
      toast.error("请输入提示词");
      return;
    }
    if (batchFolderImages.length > 0 && !batchProductImage) {
      toast.error("请先上传要替换进去的主图");
      return;
    }
    if (batchProductImage && batchFolderImages.length === 0) {
      toast.error("请先上传包含场景图的文件夹");
      return;
    }
    const targetConversation = selectedConversationId
      ? conversationsRef.current.find((conversation) => conversation.id === selectedConversationId) ?? null
      : null;
    const now = new Date().toISOString();
    const conversationId = targetConversation?.id ?? createId();
    const turnId = createId();
    const autoContext =
      !isBatchReplace && referenceImageFiles.length === 0 && targetConversation
        ? await buildAutoContextFromConversation(targetConversation, turnId)
        : null;
    const batchReplacePlan: ImageBatchReplacePlan | undefined = isBatchReplace && batchProductImage
      ? { productImage: batchProductImage, folderImages: batchFolderImages }
      : undefined;
    const effectiveReferenceImages = batchReplacePlan
      ? [batchReplacePlan.productImage, ...batchReplacePlan.folderImages]
      : referenceImages.length > 0
        ? referenceImages
        : autoContext?.referenceImages ?? [];
    const effectiveImageMode: ImageConversationMode = effectiveReferenceImages.length > 0 ? "edit" : "generate";
    const effectivePrompt = batchReplacePlan
      ? buildBatchReplacePrompt(prompt)
      : autoContext && referenceImageFiles.length === 0
        ? buildContextPrompt(autoContext.prompt, prompt)
        : prompt;
    const effectiveCount = batchReplacePlan ? batchReplacePlan.folderImages.length : parsedCount;
    const imageSize = `${imageWidth || 1024}x${imageHeight || 1024}`;

    const draftTurn: ImageTurn = {
      id: turnId,
      prompt: effectivePrompt,
      model: imageModel,
      mode: effectiveImageMode,
      referenceImages: effectiveImageMode === "edit" ? effectiveReferenceImages : [],
      batchReplace: batchReplacePlan,
      preserveSubject: effectiveImageMode === "edit" && (preserveSubject || Boolean(autoContext) || Boolean(batchReplacePlan)),
      count: effectiveCount,
      size: imageSize,
      ratio: imageRatio,
      tier: imageTier,
      quality: imageQuality,
      productId: selectedProductId ?? undefined,
      templateId: selectedTemplateId ?? undefined,
      images: batchReplacePlan
        ? createBatchReplaceLoadingImages(turnId, batchReplacePlan.folderImages)
        : createLoadingImages(turnId, parsedCount),
      createdAt: now,
      status: "queued",
    };

    const baseConversation: ImageConversation = targetConversation
      ? {
          ...targetConversation,
          updatedAt: now,
          turns: [...targetConversation.turns, draftTurn],
        }
      : {
          id: conversationId,
          title: buildConversationTitle(batchReplacePlan ? `批量换商品 ${batchReplacePlan.folderImages.length} 张` : prompt),
          createdAt: now,
          updatedAt: now,
          turns: [draftTurn],
      };

    shouldStickToBottomRef.current = true;
    const btn = scrollToLatestBtnRef.current;
    if (btn) btn.style.display = "none";
    setSelectedConversationId(conversationId);
    clearComposerInputs();

    await persistConversation(baseConversation);
    void runConversationQueue(conversationId, turnId);

    const targetStats = getImageConversationStats(baseConversation);
    if (batchReplacePlan) {
      toast.success(`已创建批量替换任务：${batchReplacePlan.folderImages.length} 张图`);
    } else if (autoContext) {
      toast.success("已自动引用上一轮结果继续生成");
    } else if (targetStats.running > 0 || targetStats.queued > 1) {
      toast.success("已并行提交到当前对话");
    } else if (!targetConversation) {
      toast.success("已创建新对话并开始处理");
    } else {
      toast.success("已发送到当前对话");
    }
  };

  const todayKey = new Date().toISOString().slice(0, 10);
  const todayGeneratedCount = conversations.reduce((total, conversation) => {
    return total + conversation.turns.reduce((turnTotal, turn) => {
      if (!turn.createdAt.startsWith(todayKey)) {
        return turnTotal;
      }
      return turnTotal + turn.images.filter((image) => image.status === "success").length;
    }, 0);
  }, 0);
  const totalGeneratedCount = conversations.reduce(
    (total, conversation) =>
      total + conversation.turns.reduce((turnTotal, turn) => turnTotal + turn.images.filter((image) => image.status === "success").length, 0),
    0,
  );
  const displayModel = imageModel === "gemini-3.1-flash-image-preview"
    ? "Nano Banana 2"
    : imageModel === "gpt-image-2-guan"
      ? "GPT Image 2 Guan"
      : imageModel;

  return (
    <>
      <section
        className={cn(
          "min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5",
          "image-single-page",
        )}
      >
        <div className="mx-auto flex w-full max-w-[1680px] flex-col gap-5">
          <div className="studio-card shrink-0 bg-white px-5 py-4 dark:bg-[#171a21]">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-[#4F7CFF]">
                  <span className="rounded-full bg-[#4F7CFF]/10 px-3 py-1">AI Creative Workspace</span>
                  <span className="rounded-full bg-[#6D5EF7]/10 px-3 py-1 text-[#6D5EF7]">
                    单图生成
                  </span>
                </div>
                <h1 className="mt-3 text-[30px] font-semibold leading-tight text-slate-950 dark:text-stone-50">
                  商品图片生成工作台
                </h1>
                <p className="mt-2 max-w-[70ch] text-[15px] leading-7 text-slate-600 dark:text-stone-300">
                  用 Prompt、商品库和参考图生成主图、场景图与社媒素材，把每一次生成沉淀成可复用的商业资产。
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:min-w-[620px] xl:grid-cols-5">
                {[
                  ["今日生成", `${todayGeneratedCount}`, "张"],
                  ["剩余额度", availableQuota, ""],
                  ["GPU", activeTaskCount > 0 ? "渲染中" : "正常", ""],
                  ["模型", displayModel, ""],
                  ["历史", `${totalGeneratedCount}`, "张"],
                ].map(([label, value, suffix]) => (
                  <div key={label} className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-3 py-3 dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="text-[12px] font-medium text-slate-500 dark:text-stone-400">{label}</div>
                    <div className="mt-1 truncate text-[18px] font-semibold text-slate-950 dark:text-stone-50">
                      {value}
                      <span className="ml-1 text-[12px] text-slate-400">{suffix}</span>
                    </div>
                  </div>
                ))}
              </div>
              <Button
                className="studio-button h-11 shrink-0 rounded-2xl bg-slate-950 px-5 text-white shadow-[0_16px_36px_rgba(17,24,39,0.16)] hover:bg-slate-800 xl:self-start"
                onClick={handleCreateDraft}
              >
                <Plus className="size-4" />
                新建任务
              </Button>
            </div>
          </div>

          <div
            className={cn(
              "grid min-h-0 grid-cols-1 items-start gap-5",
              "lg:grid-cols-1",
            )}
          >
            <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
          <DialogContent className="flex h-[min(82dvh,760px)] w-[92vw] max-w-[460px] flex-col overflow-hidden rounded-2xl border-slate-200 bg-white p-0 shadow-lg sm:rounded-2xl">
            <DialogHeader className="px-6 pt-7 pb-4 sm:px-8">
              <DialogTitle className="flex items-center gap-2 text-xl font-bold tracking-tight">
                <History className="size-5" />
                历史记录
              </DialogTitle>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 sm:px-8">
              <ImageSidebar
                conversations={conversations}
                isLoadingHistory={isLoadingHistory}
                selectedConversationId={selectedConversationId}
                onCreateDraft={() => {
                  handleCreateDraft();
                  setIsHistoryOpen(false);
                }}
                onClearHistory={openClearHistoryConfirm}
                onSelectConversation={(id) => {
                  setSelectedConversationId(id);
                  setIsHistoryOpen(false);
                }}
                onDeleteConversation={openDeleteConversationConfirm}
                onRenameConversation={handleRenameConversation}
                formatConversationTime={formatConversationTime}
                hideActionButtons
              />
            </div>
          </DialogContent>
            </Dialog>

            <div className="flex min-h-0 flex-col gap-5 pr-1">
            <SingleRecentStrip
              conversations={conversations}
              selectedConversationId={selectedConversationId}
              onSelectConversation={setSelectedConversationId}
              formatConversationTime={formatConversationTime}
            />

          <div className="studio-card flex items-center justify-between gap-2 bg-white px-3 py-3 dark:bg-[#171a21] lg:hidden">
            <Button
              variant="outline"
              className="h-10 flex-1 rounded-2xl border-black/[0.06] bg-white text-slate-700 shadow-none"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History className="mr-2 size-4" />
              历史记录 ({conversations.length})
            </Button>
            <Button
              className="h-10 rounded-2xl bg-slate-950 text-white shadow-none"
              onClick={handleCreateDraft}
            >
              <Plus className="size-4" />
              新建
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-2xl border-black/[0.06] bg-white px-3 text-slate-600 shadow-none"
              onClick={openClearHistoryConfirm}
              disabled={conversations.length === 0}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <ImageComposer
            prompt={imagePrompt}
            imageCount={imageCount}
            imageRatio={imageRatio}
            imageTier={imageTier}
            imageWidth={imageWidth}
            imageHeight={imageHeight}
            imageQuality={imageQuality}
            imageModel={imageModel}
            imageModels={imageModels}
            products={products}
            promptTemplates={promptTemplates}
            selectedProductId={selectedProductId}
            selectedTemplateId={selectedTemplateId}
            availableQuota={availableQuota}
            activeTaskCount={activeTaskCount}
            referenceImages={referenceImages}
            batchProductImage={batchProductImage}
            batchFolderImages={batchFolderImages}
            preserveSubject={preserveSubject}
            textareaRef={textareaRef}
            fileInputRef={fileInputRef}
            onPromptChange={setImagePrompt}
            onImageCountChange={(value) => setImageCount(value ? clampImageCount(value) : "")}
            onImageRatioChange={setImageRatio}
            onImageTierChange={setImageTier}
            onImageWidthChange={setImageWidth}
            onImageHeightChange={setImageHeight}
            onImageQualityChange={setImageQuality}
            onImageModelChange={setImageModel}
            onSelectedProductChange={setSelectedProductId}
            onSelectedTemplateChange={handleTemplateChange}
            onPreserveSubjectChange={setPreserveSubject}
            onSubmit={handleSubmit}
            onPickReferenceImage={() => fileInputRef.current?.click()}
            onPickBatchProductImage={handlePickBatchProductImage}
            onPickBatchFolder={handlePickBatchFolder}
            onClearBatchReplace={handleClearBatchReplace}
            onReferenceImageChange={handleReferenceImageChange}
            onRemoveReferenceImage={handleRemoveReferenceImage}
          />

          <div className="studio-card relative min-h-[520px] overflow-hidden bg-white dark:bg-[#171a21]">
            <div
              ref={resultsViewportRef}
              onScroll={handleResultsScroll}
              className="hide-scrollbar max-h-[min(780px,calc(100dvh_-_140px))] min-h-[520px] overscroll-contain overflow-y-auto bg-[#F8FAFC] px-3 py-3 dark:bg-[#111317] sm:px-5 sm:py-5"
              style={{ contain: "layout style paint" }}
            >
              <ImageResults
                selectedConversation={selectedConversation}
                onOpenLightbox={openLightbox}
                onContinueEdit={handleContinueEdit}
                onDeletePrompt={openDeletePromptConfirm}
                onDeleteResults={openDeleteResultsConfirm}
                onReuseTurnConfig={handleReuseTurnConfig}
                onRegenerateTurn={handleRegenerateTurn}
                onRetryImage={handleRetryImage}
                onCancelTurn={handleCancelTurn}
                onTimeoutRetryContinue={handleTimeoutRetryContinue}
                allowTimeoutRetryContinue={!isOpenAIRelayEnabled}
                onDismissErrors={handleDismissErrors}
                formatConversationTime={formatConversationTime}
              />
            </div>
            <UltraScrollNavigator targetRef={resultsViewportRef} />

            <button
              ref={scrollToLatestBtnRef}
              type="button"
              aria-label="滚动到最新消息"
              title="滚动到最新消息"
              onClick={() => scrollResultsToLatest("smooth")}
              className="studio-button absolute bottom-4 left-1/2 z-20 inline-flex size-11 -translate-x-1/2 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-700 shadow-[0_18px_44px_rgba(15,23,42,0.12)] hover:bg-[#4F7CFF]/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/30 dark:border-white/10 dark:bg-stone-800/95 dark:text-stone-100"
              style={{ display: "none" }}
            >
              <ArrowDown className="size-5" />
            </button>
          </div>
            </div>
          </div>
        </div>
      </section>

      <ImageLightbox
        images={lightboxImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />

      {deleteConfirm ? (
        <Dialog open onOpenChange={(open) => (!open ? setDeleteConfirm(null) : null)}>
          <DialogContent showCloseButton={false} className="rounded-2xl p-6">
            <DialogHeader className="gap-2">
              <DialogTitle>{deleteConfirmTitle}</DialogTitle>
              <DialogDescription className="text-sm leading-6">
                {deleteConfirmDescription}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                取消
              </Button>
              <Button className="bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleConfirmDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}


    </>
  );
}

export function ImageWorkspacePage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <ImagePageContent isAdmin={session.role === "admin"} />;
}

export default ImageWorkspacePage;
