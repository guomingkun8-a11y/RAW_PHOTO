<script setup lang="ts">
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Ban,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Copy,
  ExternalLink,
  Film,
  ImagePlus,
  Link2,
  LoaderCircle,
  MessageSquarePlus,
  RefreshCw,
  Sparkles,
  Trash2,
  UploadCloud,
  Video,
  X,
  XCircle,
} from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import ComposerSelect from "@/components/ComposerSelect.vue";
import { useVideoAssetRefresh, useVideoTaskPolling, videoTaskNeedsPolling } from "@/composables/useVideoTaskPolling";
import EngineSwitch from "@/components/image/EngineSwitch.vue";
import {
  cancelVideoGenerationTask,
  createVideoAgentPlan,
  createVideoGenerationTask,
  fetchAgentVideoStatuses,
  fetchSettingsConfig,
  fetchVideoAgentPlans,
  fetchVideoGenerationTasks,
  preuploadImageReferences,
  reconcileVideoGenerationTask,
  resolveApiAssetUrl,
  retryAgentVideoAnalysis,
  streamVideoAgentPlan,
  uploadAgentVideos,
  type AgentVideoAsset,
  type ReferenceUploadItem,
  type SettingsConfig,
  type VideoAgentAttachment,
  type VideoAgentPlan,
  type VideoGenerationDuration,
  type VideoGenerationTask,
} from "@/lib/api";
import { storageKey } from "@/lib/storage-namespace";
import {
  DEFAULT_VIDEO_MODEL,
  formatVideoAspectRatio,
  formatVideoDuration,
  formatVideoModelOption,
  normalizeVideoModelSpecs,
  videoFrameRole,
  videoModelOptionLabel,
  videoModelUsesFirstLastFrames,
} from "@/lib/video-models";
import { sessionState } from "@/stores/session";

type VideoComposerMode = "text_to_video" | "image_to_video" | "agent";
type VideoWorkspace = "generation" | "agent";
type AgentFeedbackStatusId = "starting" | "thinking" | "completed" | "failed" | "canceled";
type AgentFeedbackStatus = {
  id: AgentFeedbackStatusId;
  emoji: string;
  label: string;
};
type ReferencePreviewItem = {
  id: string;
  kind: "upload";
  url: string;
  label: string;
  meta: string;
  role: string;
};
type AgentVideoPreviewItem = {
  id: string;
  asset: AgentVideoAsset;
};

const props = withDefaults(defineProps<{
  workspace?: VideoWorkspace;
}>(), {
  workspace: "generation",
});

const route = useRoute();
const router = useRouter();
const isAgentWorkspace = computed(() => props.workspace === "agent");
const initialMode: VideoComposerMode = props.workspace === "agent" ? "agent" : "text_to_video";
const VIDEO_MODEL_STORAGE_KEY = storageKey("gmkraw:video_generation_model");
const activeConversationStorageKey = storageKey(
  props.workspace === "agent" ? "gmkraw:video_agent_active_conversation" : "gmkraw:video_active_conversation",
);
const VIDEO_ANALYSIS_POLL_INTERVAL_MS = 3000;
const AGENT_PLAN_POLL_INTERVAL_MS = 2500;
const AGENT_VIDEO_MAX_ITEMS = 4;
const TERMINAL_STATUSES = new Set(["success", "error", "canceled"]);
const AGENT_TERMINAL_STATUSES = new Set(["completed", "failed", "canceled"]);
const VIDEO_ANALYSIS_ACTIVE_STATUSES = new Set(["pending", "queued", "processing", "analyzing"]);

const prompt = ref("");
const model = ref(localStorage.getItem(VIDEO_MODEL_STORAGE_KEY) || DEFAULT_VIDEO_MODEL);
const mode = ref<VideoComposerMode>(initialMode);
const aspectRatio = ref("16:9");
const durationSecs = ref<VideoGenerationDuration>(5);
const quality = ref("");
const uploadedReferences = ref<ReferenceUploadItem[]>([]);
const agentVideos = ref<AgentVideoAsset[]>([]);
const referenceInput = ref<HTMLInputElement | null>(null);
const videoInput = ref<HTMLInputElement | null>(null);
const textarea = ref<HTMLTextAreaElement | null>(null);
const resultsViewport = ref<HTMLDivElement | null>(null);
const settingsConfig = ref<SettingsConfig | null>(null);
const tasks = ref<VideoGenerationTask[]>([]);
const agentPlans = ref<VideoAgentPlan[]>([]);
const optimisticAgentPlans = ref<VideoAgentPlan[]>([]);
const reasoningEnabled = ref(false);
const loading = ref(true);
const activeConversationId = ref(localStorage.getItem(activeConversationStorageKey) || "");
const refreshing = ref(false);
const submitting = ref(false);
const uploadingReferences = ref(false);
const uploadingVideos = ref(false);
const retryingVideoIds = ref<Set<string>>(new Set());
const cancelingIds = ref<Set<string>>(new Set());
const reconcilingIds = ref<Set<string>>(new Set());
const isFocused = ref(false);
const isDragging = ref(false);
const showScrollLatest = ref(false);
const highlightedTaskId = ref("");
const taskNextCursor = ref<string | null>(null);
const loadingOlderTasks = ref(false);
let disposed = false;
let pendingSubmission: { fingerprint: string; clientTaskId: string } | null = null;
let videoAnalysisPollTimer = 0;
let agentPlanPollTimer = 0;
let videoAnalysisPollInFlight = false;
let agentPlanPollInFlight = false;
let loadRequestId = 0;
let highlightTimer = 0;
let lastFocusedRouteTaskId = "";
const agentFeedbackNow = ref(Date.now());
let agentFeedbackTimer = 0;

const modeTitlePool: Record<VideoComposerMode, string[]> = {
  text_to_video: [
    "描述镜头运动和主体动作。",
    "一句话生成一段完整短片。",
    "写下场景、动作和镜头节奏。",
    "从画面想法开始生成视频。",
    "输入创意，生成第一版视频。",
    "描述一个开场镜头。",
    "写下你想要的成片氛围。",
    "让一个商品场景动起来。",
    "把短视频创意写成画面。",
    "描述主体出现和转场方式。",
    "指定镜头推拉和运动节奏。",
    "生成一段适合投放的短片。",
    "写出人物、场景和动作。",
    "描述光线、风格和构图。",
    "从一句脚本开始生成视频。",
  ],
  image_to_video: [
    "上传参考图，让静态画面动起来。",
    "给产品图补充镜头运动。",
    "描述参考图该怎么动。",
    "让图片里的主体自然运动。",
    "用参考图生成动态镜头。",
    "把商品主图变成短视频。",
    "让参考图产生自然镜头感。",
    "指定图片里的运动方向。",
    "给静态素材添加节奏变化。",
    "描述主体如何出现和移动。",
    "让画面从参考图延展出来。",
    "用图片生成展示镜头。",
    "让产品细节动得更清楚。",
    "把参考图做成投放短片。",
    "补充背景、动作和镜头轨迹。",
  ],
  agent: [
    "你好，有什么可以帮你？",
    "今天想聊些什么？",
    "需要我帮你做什么？",
  ],
};
const modeTitleCursor: Record<VideoComposerMode, number> = {
  text_to_video: Math.floor(Math.random() * modeTitlePool.text_to_video.length),
  image_to_video: Math.floor(Math.random() * modeTitlePool.image_to_video.length),
  agent: Math.floor(Math.random() * modeTitlePool.agent.length),
};

const idleTitle = ref(nextModeTitle(initialMode));
const idleTitleChars = computed(() => idleTitle.value.split(""));
const idleTitleVisibleChars = ref<boolean[]>([]);
const prefersReducedMotion = ref(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
let idleTitleTimers: number[] = [];

const baseVideoModeOptions = [
  { value: "text_to_video", label: "文生", icon: Video },
  { value: "image_to_video", label: "图生", icon: ImagePlus },
];

const currentUserName = computed(() => sessionState.session?.name || sessionState.session?.username || "用户");
const currentUserInitial = computed(() => currentUserName.value.trim().slice(0, 1).toUpperCase() || "U");
const videoSettings = computed(() => settingsConfig.value?.video_generation);
const videoModelSpecs = computed(() => normalizeVideoModelSpecs(videoSettings.value?.models));
const videoModeOptions = baseVideoModeOptions;
const configProblem = computed(() => {
  const settings = videoSettings.value;
  if (!settings) return "";
  if (!settings.enabled) return "视频生成未启用";
  if (!String(settings.base_url || "").trim()) return "未配置视频生成接口";
  if (!settings.has_api_key && !Number(settings.api_key_count || 0)) return "未配置视频生成密钥";
  return "";
});
const uploadedReferenceUrls = computed(() => uploadedReferences.value.map((item) => item.url).filter(Boolean));
const canUseReferenceImages = computed(() => mode.value === "image_to_video");
const canUploadAgentImages = computed(() => mode.value === "agent");
const canUploadImages = computed(() => canUseReferenceImages.value || canUploadAgentImages.value);
const canUploadAgentVideos = computed(() => mode.value === "agent");
const canDropMedia = computed(() => canUploadImages.value || canUploadAgentVideos.value);
const imageUrls = computed(() => canUseReferenceImages.value ? Array.from(new Set(uploadedReferenceUrls.value)) : []);
const agentImageUrls = computed(() => canUploadAgentImages.value ? Array.from(new Set(uploadedReferenceUrls.value)) : []);
const composerImageUrls = computed(() => canUploadAgentImages.value ? agentImageUrls.value : imageUrls.value);
const referenceItems = computed<ReferencePreviewItem[]>(() => {
  if (!canUploadImages.value) return [];
  const spec = mode.value === "image_to_video" ? selectedVideoModelSpec.value : null;
  return uploadedReferences.value
    .filter((item) => item.url)
    .map((item, index) => {
      const role = videoFrameRole(spec, index);
      const filename = item.filename || "参考图";
      return {
        id: `upload-${item.sha256 || item.url}`,
        kind: "upload" as const,
        url: item.url,
        label: role ? `${role} · ${filename}` : filename,
        meta: fileSize(item.file_size) || "已上传",
        role,
      };
    });
});
const agentVideoItems = computed<AgentVideoPreviewItem[]>(() => agentVideos.value.map((asset) => ({
  id: asset.videoId,
  asset,
})));
const modelCapabilityMode = computed<"text_to_video" | "image_to_video">(() => {
  if (mode.value === "image_to_video") return "image_to_video";
  if (mode.value === "agent" && imageUrls.value.length) return "image_to_video";
  return "text_to_video";
});
const activeVideoModelSpecs = computed(() =>
  videoModelSpecs.value.filter((item) => item.modes.includes(modelCapabilityMode.value)),
);
const modelSelectOptions = computed(() =>
  activeVideoModelSpecs.value.map((item) => ({ value: item.id, label: item.label })),
);
const selectedVideoModelSpec = computed(() =>
  activeVideoModelSpecs.value.find((item) => item.id === model.value)
    || activeVideoModelSpecs.value[0]
    || null,
);
const selectedModelId = computed(() => selectedVideoModelSpec.value?.id || "");
const aspectOptions = computed(() => selectedVideoModelSpec.value?.aspect_ratios || []);
const durationOptions = computed(() => selectedVideoModelSpec.value?.durations || []);
const aspectSelectOptions = computed(() => aspectOptions.value.map((value) => ({
  value,
  label: formatVideoAspectRatio(value),
})));
const durationSelectOptions = computed(() => durationOptions.value.map((value) => ({
  value: String(value),
  label: formatVideoDuration(value),
})));
const qualityOptions = computed(() =>
  selectedVideoModelSpec.value
    ? selectedVideoModelSpec.value.options.map((value) => ({
      value,
      label: formatVideoModelOption(value, selectedVideoModelSpec.value!),
    }))
    : [],
);
const modelOptionLabel = computed(() => selectedVideoModelSpec.value ? videoModelOptionLabel(selectedVideoModelSpec.value) : "参数");
const showModelControls = computed(() => mode.value !== "agent" && Boolean(selectedVideoModelSpec.value));
const isFirstLastFrameModel = computed(() => mode.value === "image_to_video"
  && videoModelUsesFirstLastFrames(selectedVideoModelSpec.value));
const referenceImageMinimum = computed(() => mode.value === "image_to_video"
  ? Math.max(1, Number(selectedVideoModelSpec.value?.min_images || 1))
  : 0);
const referenceImageMaximum = computed(() => {
  if (mode.value === "image_to_video") {
    return Math.max(referenceImageMinimum.value, Number(selectedVideoModelSpec.value?.max_images || 1));
  }
  return mode.value === "agent" ? 8 : 0;
});
const referenceImageError = computed(() => {
  if (!canUploadImages.value) return "";
  const count = composerImageUrls.value.length;
  const maximum = referenceImageMaximum.value;
  if (mode.value === "agent") {
    return count > maximum ? `智能体最多支持 ${maximum} 张参考图` : "";
  }
  const spec = selectedVideoModelSpec.value;
  if (!spec) return "";
  if (isFirstLastFrameModel.value) {
    if (count < 1) return `${spec.label} 请先上传首帧`;
    if (count > 2) return `${spec.label} 最多上传首帧和尾帧，共 2 张`;
    return "";
  }
  const minimum = referenceImageMinimum.value;
  if (minimum === maximum && count !== minimum) return `${spec.label} 仅支持 ${minimum} 张参考图`;
  if (count < minimum) return `${spec.label} 至少需要 ${minimum} 张参考图`;
  if (count > maximum) return `${spec.label} 最多支持 ${maximum} 张参考图`;
  return "";
});
const referenceUploadLabel = computed(() => {
  if (canUploadAgentImages.value) return "图片";
  return isFirstLastFrameModel.value ? "首/尾帧" : "参考图";
});
const referenceUploadTitle = computed(() => isFirstLastFrameModel.value
  ? "第 1 张为首帧，第 2 张为尾帧，最多 2 张"
  : `最多 ${referenceImageMaximum.value} 张参考图`);
function taskConversationKey(task: VideoGenerationTask) {
  return task.conversation_id || `legacy:${task.id}`;
}
const currentTasks = computed(() => {
  if (isAgentWorkspace.value || !activeConversationId.value) return [];
  return tasks.value.filter((task) => taskConversationKey(task) === activeConversationId.value);
});
const activeTasks = computed(() => currentTasks.value.filter(videoTaskNeedsPolling));
const activeTaskCount = computed(() => (
  isAgentWorkspace.value
    ? tasks.value.filter(videoTaskNeedsPolling).length
    : activeTasks.value.length
));
const successTaskCount = computed(() => currentTasks.value.filter((item) => item.status === "success").length);
const failedTaskCount = computed(() => currentTasks.value.filter((item) => item.status === "error").length);
const sortedTasks = computed(() =>
  [...currentTasks.value].sort((a, b) => timestamp(a.created_at || a.updated_at) - timestamp(b.created_at || b.updated_at)),
);
const sortedAgentPlans = computed(() =>
  [...agentPlans.value, ...optimisticAgentPlans.value]
    .filter((plan) => plan.conversation_id === activeConversationId.value)
    .filter((plan, index, all) => {
      return all.findIndex((item) => sameAgentPlan(item, plan)) === index;
    })
    .sort((a, b) => timestamp(a.created_at) - timestamp(b.created_at)),
);
const hasActiveAgentPlan = computed(() =>
  sortedAgentPlans.value.some((plan) => !AGENT_TERMINAL_STATUSES.has(plan.status)),
);
const messageCount = computed(() => sortedTasks.value.length + sortedAgentPlans.value.length);
const hasMessages = computed(() => messageCount.value > 0);
const selectedQualityLabel = computed(() =>
  selectedVideoModelSpec.value
    ? qualityOptions.value.find((item) => item.value === quality.value)?.label || quality.value
    : "",
);
const activeModelSummary = computed(() => {
  return selectedVideoModelSpec.value?.label || "图生模型待配置";
});
const composerSummary = computed(() => {
  if (mode.value === "agent") return "视频智能体";
  if (!showModelControls.value) return `${modeLabel(mode.value)} · ${activeModelSummary.value}`;
  return `${modeLabel(mode.value)} · ${activeModelSummary.value} · ${formatVideoAspectRatio(aspectRatio.value)} · ${formatVideoDuration(durationSecs.value)} · ${selectedQualityLabel.value}`;
});
function videoAnalysisStatus(asset: AgentVideoAsset) {
  return String(asset.analysisStatus || asset.status || "pending").toLowerCase();
}

const failedAgentVideos = computed(() => agentVideos.value.filter((asset) => videoAnalysisStatus(asset) === "failed"));
const hasAgentMedia = computed(() => uploadedReferences.value.some((item) => Boolean(item.url)) || agentVideos.value.some((asset) => Boolean(asset.videoId && asset.url)));
const hasAgentInput = computed(() => Boolean(prompt.value.trim()) || hasAgentMedia.value);
const canSubmit = computed(() => {
  if (uploadingReferences.value || uploadingVideos.value) return false;
  if (mode.value === "agent") return hasAgentInput.value && !failedAgentVideos.value.length && !referenceImageError.value;
  if (submitting.value) return false;
  return (
    !configProblem.value
    && Boolean(selectedVideoModelSpec.value)
    && Boolean(prompt.value.trim())
    && Boolean(selectedModelId.value)
    && !referenceImageError.value
  );
});
const submitDisabledReason = computed(() => {
  if (submitting.value) return "正在提交";
  if (uploadingReferences.value) return "参考图上传中";
  if (uploadingVideos.value) return "视频上传中";
  if (mode.value === "agent") {
    if (!hasAgentInput.value) return "请输入消息";
    if (failedAgentVideos.value.length) return "有视频解析失败，请重试";
    return "";
  }
  if (configProblem.value) return configProblem.value;
  if (!selectedVideoModelSpec.value) return mode.value === "image_to_video" ? "图生模型暂未配置" : "文生模型暂未配置";
  if (!selectedModelId.value) return "当前模式没有可用模型";
  if (referenceImageError.value) return referenceImageError.value;
  if (!prompt.value.trim()) return "请输入视频描述";
  return "";
});
const composerPlaceholder = computed(() => {
  if (mode.value === "agent") return "输入消息...";
  if (isFirstLastFrameModel.value) return "描述首帧到尾帧之间的动作、镜头运动和节奏...";
  if (mode.value === "image_to_video") return "描述参考图要如何运动，补充镜头、节奏和风格...";
  return "描述画面、主体动作、镜头运动和风格...";
});
const submitTitle = computed(() => submitDisabledReason.value || (mode.value === "agent" ? "发送消息" : "提交视频任务"));
const taskSignal = computed(() => [
  ...sortedTasks.value.map((task) => `${task.id}:${task.status}:${task.updated_at || ""}`),
  ...sortedAgentPlans.value.map((plan) => `${plan.id}:${plan.turn_id || ""}:${plan.status}:${plan.message?.length || 0}:${plan.reasoning_summary?.length || 0}:${plan.analysis_error || ""}`),
  ...(isAgentWorkspace.value ? tasks.value.map((task) => `${task.id}:${task.status}:${task.updated_at || ""}`) : []),
].join("|"));


function timestamp(value?: string) {
  const time = value ? new Date(value).getTime() : 0;
  return Number.isFinite(time) ? time : 0;
}

function agentFeedbackStatus(plan: VideoAgentPlan): AgentFeedbackStatus {
  const status = String(plan.status || "").toLowerCase();
  if (status === "analyzing" || status === "pending") {
    return { id: "starting", emoji: "◌", label: "视频解析中" };
  }
  if (status === "responding" && !plan.reasoning_enabled) {
    return { id: "thinking", emoji: "✦", label: "正在生成回答" };
  }
  if (status === "completed") return { id: "completed", emoji: "✅", label: "思考完成" };
  if (status === "failed") return { id: "failed", emoji: "❌", label: "思考遇到问题" };
  if (status === "canceled") return { id: "canceled", emoji: "✖️", label: "中断思考" };
  if (status === "pending" || status === "connecting") {
    return { id: "starting", emoji: "💡", label: "开始思考" };
  }
  return { id: "thinking", emoji: "✨", label: "思考中..." };
}

function agentFeedbackDetail(plan: VideoAgentPlan) {
  if (plan.status === "analyzing" || plan.status === "pending") return "解析完成后自动生成回答";
  if (plan.status === "responding" && !plan.reasoning_enabled) return "视频已解析，正在请求大模型";
  if (plan.status === "completed") return plan.duration_ms ? `用时 ${formatDuration(plan.duration_ms)}` : "已生成回答";
  if (plan.status === "failed") return plan.analysis_error || plan.message || "请稍后重试";
  if (plan.status === "canceled") return "本次思考已停止";
  return formatAgentElapsed(plan);
}

function formatAgentElapsed(plan: VideoAgentPlan) {
  const startedAt = timestamp(plan.created_at);
  if (!startedAt) return "";
  const seconds = Math.max(0, Math.floor((agentFeedbackNow.value - startedAt) / 1000));
  if (seconds < 1) return "刚刚开始";
  if (seconds < 60) return `已用时 ${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `已用时 ${minutes} 分 ${remainder} 秒` : `已用时 ${minutes} 分钟`;
}

function syncAgentFeedbackTimer() {
  if (hasActiveAgentPlan.value && !agentFeedbackTimer) {
    agentFeedbackTimer = window.setInterval(() => {
      agentFeedbackNow.value = Date.now();
    }, 1000);
  }
  if (!hasActiveAgentPlan.value && agentFeedbackTimer) {
    window.clearInterval(agentFeedbackTimer);
    agentFeedbackTimer = 0;
  }
}

function clearIdleTitleAnimation() {
  for (const timer of idleTitleTimers) window.clearTimeout(timer);
  idleTitleTimers = [];
}

function playIdleTitleAnimation() {
  clearIdleTitleAnimation();
  const chars = idleTitleChars.value;
  idleTitleVisibleChars.value = chars.map(() => false);
  if (prefersReducedMotion.value) {
    idleTitleVisibleChars.value = chars.map(() => true);
    return;
  }
  chars.forEach((_, index) => {
    const timer = window.setTimeout(() => {
      const next = [...idleTitleVisibleChars.value];
      next[index] = true;
      idleTitleVisibleChars.value = next;
    }, index * 60);
    idleTitleTimers.push(timer);
  });
}

function nextModeTitle(value: VideoComposerMode) {
  const titles = modeTitlePool[value];
  const index = modeTitleCursor[value] % titles.length;
  modeTitleCursor[value] = index + 1;
  return titles[index] || "";
}

function updateIdleTitle(value: VideoComposerMode) {
  idleTitle.value = nextModeTitle(value);
  if (!hasMessages.value) playIdleTitleAnimation();
}

function createClientTaskId() {
  const randomPart = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 10)
    : Math.random().toString(16).slice(2, 12);
  return `video-${Date.now()}-${randomPart}`;
}

function createClientConversationId() {
  const randomPart = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
    : Math.random().toString(16).slice(2, 14);
  return `video-conversation-${Date.now()}-${randomPart}`;
}

function setActiveConversation(id: string) {
  activeConversationId.value = id;
  if (id) localStorage.setItem(activeConversationStorageKey, id);
  else localStorage.removeItem(activeConversationStorageKey);
}

function ensureActiveConversation() {
  if (activeConversationId.value) return;
  const latest = [...tasks.value]
    .sort((a, b) => timestamp(b.updated_at || b.created_at) - timestamp(a.updated_at || a.created_at))[0];
  if (latest) setActiveConversation(taskConversationKey(latest));
}

function ensureWritableConversation() {
  if (activeConversationId.value && !activeConversationId.value.startsWith("legacy:")) {
    return activeConversationId.value;
  }
  const id = createClientConversationId();
  setActiveConversation(id);
  return id;
}

function routeTaskId() {
  const value = route.query.task;
  return String(Array.isArray(value) ? value[0] || "" : value || "").trim();
}

function routeConversationId() {
  const value = route.query.conversation;
  return String(Array.isArray(value) ? value[0] || "" : value || "").trim();
}

function videoTaskDomId(taskId: string) {
  return `video-task-${encodeURIComponent(taskId)}`;
}

function setHighlightedTask(taskId: string) {
  highlightedTaskId.value = taskId;
  if (highlightTimer) window.clearTimeout(highlightTimer);
  highlightTimer = window.setTimeout(() => {
    if (highlightedTaskId.value === taskId) highlightedTaskId.value = "";
  }, 2600);
}

async function focusRouteTask(behavior: ScrollBehavior = "smooth", force = false) {
  const taskId = routeTaskId();
  if (!taskId) return false;
  const task = tasks.value.find((item) => item.id === taskId);
  if (!task) return false;
  const conversationId = taskConversationKey(task);
  if (!force && lastFocusedRouteTaskId === taskId && activeConversationId.value === conversationId) return true;
  if (activeConversationId.value !== conversationId) setActiveConversation(conversationId);
  await nextTick();
  await nextTick();
  const container = resultsViewport.value;
  const element = document.getElementById(videoTaskDomId(taskId));
  if (!container || !element) return false;
  const containerRect = container.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  const top = Math.max(0, container.scrollTop + elementRect.top - containerRect.top - 12);
  container.scrollTo({ top, behavior });
  showScrollLatest.value = false;
  lastFocusedRouteTaskId = taskId;
  setHighlightedTask(taskId);
  return true;
}

function videoUrl(task: VideoGenerationTask) {
  return task.video_url || task.data?.find((item) => item.url)?.url || "";
}

function coverUrl(task: VideoGenerationTask) {
  return task.cover_url || task.data?.find((item) => item.cover_url)?.cover_url || "";
}

function mediaUrl(value?: string) {
  return resolveApiAssetUrl(value || "");
}

function modeLabel(value: string) {
  if (value === "agent") return "智能体";
  return value === "image_to_video" ? "图生视频" : "文生视频";
}

function statusLabel(status: VideoGenerationTask["status"] | string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "生成中",
    success: "已完成",
    error: "失败",
    canceled: "已取消",
  };
  return labels[status] || status || "未知";
}

function statusDetail(task: VideoGenerationTask) {
  if (task.cancellation_pending) return "已请求本地取消，上游可能仍在生成并计费，正在核对结果。";
  if (task.reconciliation_required) return task.error || "提交结果待核对，请勿重复生成。";
  if (task.error) return task.error;
  if (task.status === "success") return "视频已生成，可以预览、复制链接或继续复用参数。";
  if (task.status === "running") return task.progress || "正在生成视频，请保持页面打开以便查看最新状态。";
  if (task.status === "queued") return task.progress || "任务已进入队列，系统会自动刷新状态。";
  if (task.status === "canceled") return "任务已取消。";
  return task.progress || "等待处理。";
}

function statusClass(status: VideoGenerationTask["status"] | string) {
  if (status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300";
  if (status === "running") return "bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]";
  if (status === "queued") return "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300";
  if (status === "error") return "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
  return "bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-stone-300";
}

function statusIcon(status: VideoGenerationTask["status"] | string) {
  if (status === "success") return CheckCircle2;
  if (status === "error") return XCircle;
  if (status === "canceled") return Ban;
  if (status === "running") return LoaderCircle;
  return Clock3;
}

function formatDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDuration(ms?: number) {
  if (!ms) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatCost(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "";
  return `￥${value.toFixed(value >= 1 ? 3 : 4)}`;
}

function fileSize(bytes?: number) {
  if (!bytes) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function taskMeta(task: VideoGenerationTask) {
  return [
    modeLabel(task.mode),
    task.model || "unknown",
    task.aspect_ratio || "16:9",
    formatVideoDuration(task.duration_secs ?? 5),
    task.quality || "",
  ].filter(Boolean);
}

function mergeTask(task: VideoGenerationTask) {
  tasks.value = [task, ...tasks.value.filter((item) => item.id !== task.id)];
}

function sameAgentPlan(left: VideoAgentPlan, right: VideoAgentPlan) {
  if (left.conversation_id && right.conversation_id && left.conversation_id !== right.conversation_id) {
    return false;
  }
  return Boolean(
    (left.id && right.id && left.id === right.id)
    || (left.turn_id && right.turn_id && left.turn_id === right.turn_id),
  );
}

function upsertAgentPlan(plan: VideoAgentPlan, optimistic = false) {
  if (!plan.id && !plan.turn_id) return;
  if (optimistic) {
    const index = optimisticAgentPlans.value.findIndex((item) => sameAgentPlan(item, plan));
    if (agentPlans.value.some((item) => sameAgentPlan(item, plan))) return;
    if (index < 0) optimisticAgentPlans.value = [...optimisticAgentPlans.value, plan];
    else {
      const next = [...optimisticAgentPlans.value];
      next[index] = { ...next[index], ...plan };
      optimisticAgentPlans.value = next;
    }
    return;
  }
  const index = agentPlans.value.findIndex((item) => sameAgentPlan(item, plan));
  if (index < 0) agentPlans.value = [...agentPlans.value, plan];
  else {
    const next = [...agentPlans.value];
    next[index] = { ...next[index], ...plan };
    agentPlans.value = next;
  }
  optimisticAgentPlans.value = optimisticAgentPlans.value.filter((item) => !sameAgentPlan(item, plan));
}

function updateAgentPlan(plan: VideoAgentPlan, patch: Partial<VideoAgentPlan>) {
  const update = (items: VideoAgentPlan[]) => items.map((item) => (
    sameAgentPlan(item, plan) ? { ...item, ...patch } : item
  ));
  agentPlans.value = update(agentPlans.value);
  optimisticAgentPlans.value = update(optimisticAgentPlans.value);
}

function replaceAgentPlans(items: VideoAgentPlan[], conversationId = activeConversationId.value) {
  const scopedItems = items.filter((item) => item.conversation_id === conversationId);
  agentPlans.value = scopedItems;
  optimisticAgentPlans.value = optimisticAgentPlans.value.filter(
    (local) => local.conversation_id === conversationId
      && !scopedItems.some((remote) => sameAgentPlan(local, remote)),
  );
}

async function loadSettings() {
  try {
    settingsConfig.value = (await fetchSettingsConfig()).config;
  } catch (error) {
    settingsConfig.value = null;
  }
}

async function loadTasks(showSpinner = false) {
  const currentId = ++loadRequestId;
  const currentItems = isAgentWorkspace.value ? agentPlans.value : tasks.value;
  if (showSpinner && !currentItems.length) loading.value = true;
  else refreshing.value = true;
  try {
    if (isAgentWorkspace.value) {
      const routeConversation = routeConversationId();
      const targetConversationId = routeConversation || activeConversationId.value;
      const response = await fetchVideoAgentPlans({
        limit: targetConversationId ? 200 : 500,
        conversationId: targetConversationId || undefined,
      });
      if (currentId !== loadRequestId) return;
      let selectedConversationId = targetConversationId;
      if (!selectedConversationId) {
        const latest = [...response.items].sort(
          (a, b) => timestamp(b.created_at) - timestamp(a.created_at),
        )[0];
        selectedConversationId = String(latest?.conversation_id || "").trim();
      }
      if (selectedConversationId) setActiveConversation(selectedConversationId);
      const scopedPlans = selectedConversationId
        ? response.items.filter((item) => item.conversation_id === selectedConversationId)
        : [];
      replaceAgentPlans(scopedPlans);
      const generationIds = generationTaskIdsForPlans(scopedPlans);
      if (!generationIds.length) {
        tasks.value = [];
        return;
      }
      tasks.value = agentGenerationTasksFromResponse(scopedPlans, [], generationIds, "queued");
      const generationResponse = await fetchVideoGenerationTasks(generationIds);
      if (currentId !== loadRequestId || activeConversationId.value !== selectedConversationId) return;
      tasks.value = agentGenerationTasksFromResponse(
        scopedPlans,
        generationResponse.items,
        generationResponse.missing_ids,
      );
      return;
    }
    let targetConversationId = routeConversationId() || activeConversationId.value;
    const targetTaskId = routeTaskId();
    if (targetTaskId || !targetConversationId) {
      const latest = await fetchVideoGenerationTasks(targetTaskId ? [targetTaskId] : [], { limit: 1 });
      if (currentId !== loadRequestId || disposed) return;
      if (latest.items[0]) targetConversationId = taskConversationKey(latest.items[0]);
    }
    if (!targetConversationId) {
      tasks.value = [];
      taskNextCursor.value = null;
      return;
    }
    const response = await fetchVideoGenerationTasks(
      targetConversationId.startsWith("legacy:") ? [targetConversationId.slice(7)] : [],
      { conversationId: targetConversationId, limit: 50 },
    );
    if (currentId !== loadRequestId || disposed) return;
    taskNextCursor.value = response.has_more ? response.next_cursor || null : null;
    let nextTasks = response.items;
    if (targetTaskId && !nextTasks.some((task) => task.id === targetTaskId)) {
      const targetResponse = await fetchVideoGenerationTasks([targetTaskId]);
      if (currentId !== loadRequestId) return;
      const existingIds = new Set(targetResponse.items.map((task) => task.id));
      nextTasks = [
        ...targetResponse.items,
        ...nextTasks.filter((task) => !existingIds.has(task.id)),
      ];
    }
    tasks.value = nextTasks;
    const targetTask = targetTaskId ? nextTasks.find((task) => task.id === targetTaskId) : null;
    if (targetConversationId) setActiveConversation(targetConversationId);
    else if (targetTask) setActiveConversation(taskConversationKey(targetTask));
    else ensureActiveConversation();
    void focusRouteTask("auto");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : isAgentWorkspace.value ? "读取对话历史失败" : "读取视频任务失败");
  } finally {
    if (currentId === loadRequestId) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}

async function refreshAll(showSpinner = false) {
  if (refreshing.value) return;
  await Promise.all([loadSettings(), loadTasks(showSpinner)]);
}

const taskPolling = useVideoTaskPolling(
  () => loading.value || refreshing.value ? [] : isAgentWorkspace.value ? tasks.value : currentTasks.value,
  (updates) => updates.forEach(mergeTask),
);
const videoAssets = useVideoAssetRefresh(() => tasks.value, (updates) => updates.forEach(mergeTask));

async function refreshVideoAsset(taskId: string) {
  try { await videoAssets.refresh(taskId); }
  catch (error) { toast.error(error instanceof Error ? error.message : "刷新视频链接失败"); }
}

async function loadOlderTasks() {
  if (!taskNextCursor.value || loadingOlderTasks.value) return;
  const conversationId = activeConversationId.value;
  const requestId = loadRequestId;
  loadingOlderTasks.value = true;
  try {
    const response = await fetchVideoGenerationTasks([], {
      conversationId, limit: 50, cursor: taskNextCursor.value,
    });
    if (disposed || requestId !== loadRequestId || conversationId !== activeConversationId.value) return;
    const currentIds = new Set(tasks.value.map((task) => task.id));
    tasks.value = [...tasks.value, ...response.items.filter((task) => !currentIds.has(task.id))];
    taskNextCursor.value = response.has_more ? response.next_cursor || null : null;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取更早的视频任务失败");
  } finally {
    loadingOlderTasks.value = false;
  }
}

function updateAgentPlanPolling(delay = AGENT_PLAN_POLL_INTERVAL_MS) {
  if (agentPlanPollTimer) {
    window.clearTimeout(agentPlanPollTimer);
    agentPlanPollTimer = 0;
  }
  if (!isAgentWorkspace.value || !hasActiveAgentPlan.value || !activeConversationId.value) return;
  agentPlanPollTimer = window.setTimeout(() => {
    agentPlanPollTimer = 0;
    void pollAgentPlans();
  }, Math.max(500, delay));
}

async function pollAgentPlans() {
  if (!isAgentWorkspace.value || !activeConversationId.value) return;
  if (agentPlanPollInFlight) {
    updateAgentPlanPolling();
    return;
  }
  const conversationId = activeConversationId.value;
  agentPlanPollInFlight = true;
  try {
    const response = await fetchVideoAgentPlans({ conversationId, limit: 200 });
    if (activeConversationId.value === conversationId) {
      const scopedPlans = response.items.filter((item) => item.conversation_id === conversationId);
      replaceAgentPlans(scopedPlans);
      await refreshAgentGenerationTasks(scopedPlans, conversationId);
    }
  } catch {
    // The next poll retries without interrupting the composer.
  } finally {
    agentPlanPollInFlight = false;
    updateAgentPlanPolling();
  }
}

function scrollLatest(behavior: ScrollBehavior = "smooth") {
  const element = resultsViewport.value;
  if (!element) return;
  element.scrollTo({ top: element.scrollHeight, behavior });
  showScrollLatest.value = false;
}

function onResultsScroll() {
  const element = resultsViewport.value;
  if (!element) return;
  showScrollLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight > 160;
}

function resizeTextarea() {
  const element = textarea.value;
  if (!element) return;
  element.style.height = "auto";
  const maxHeight = window.innerWidth < 640 ? 150 : 180;
  element.style.height = `${Math.min(Math.max(element.scrollHeight, 54), maxHeight)}px`;
  element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
}

function pickReferenceImages() {
  if (!canUploadImages.value) return;
  referenceInput.value?.click();
}

function pickAgentVideos() {
  if (!canUploadAgentVideos.value) return;
  videoInput.value?.click();
}

function isImageFile(file: File) {
  return file.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp)$/i.test(file.name);
}

async function uploadReferenceFiles(files: File[]) {
  if (!canUploadImages.value) {
    toast.error("当前模式不支持图片附件");
    return;
  }
  const imageFiles = files.filter(isImageFile);
  if (!imageFiles.length) {
    toast.error("请选择图片文件");
    return;
  }
  const maximum = referenceImageMaximum.value;
  if (composerImageUrls.value.length + imageFiles.length > maximum) {
    const label = mode.value === "image_to_video" ? selectedVideoModelSpec.value?.label : "智能体";
    toast.error(isFirstLastFrameModel.value
      ? `${label || "当前模式"}最多上传首帧和尾帧，共 2 张`
      : `${label || "当前模式"}最多支持 ${maximum} 张参考图`);
    return;
  }
  uploadingReferences.value = true;
  try {
    const response = await preuploadImageReferences(imageFiles);
    const existing = new Set(uploadedReferences.value.map((item) => item.url));
    uploadedReferences.value = [
      ...uploadedReferences.value,
      ...response.items.filter((item) => item.url && !existing.has(item.url)),
    ];
    toast.success(
      response.cache_hits
        ? `${referenceUploadLabel.value}已就绪，命中缓存 ${response.cache_hits} 张`
        : `${referenceUploadLabel.value}已上传`,
    );
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "上传参考图失败");
  } finally {
    uploadingReferences.value = false;
  }
}

function onReferenceInput(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (files.length) void uploadReferenceFiles(files);
}

function onPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []).filter(isImageFile);
  if (!files.length) return;
  event.preventDefault();
  void uploadReferenceFiles(files);
}

function onDrop(event: DragEvent) {
  event.preventDefault();
  isDragging.value = false;
  const files = Array.from(event.dataTransfer?.files || []);
  if (!files.length || !canDropMedia.value) return;
  const images = files.filter(isImageFile);
  const videos = files.filter(isVideoFile);
  if (images.length) void uploadReferenceFiles(images);
  if (videos.length) void uploadAgentVideoFiles(videos);
  if (!images.length && !videos.length) toast.error("请选择图片或视频文件");
}

function removeReferenceItem(item: ReferencePreviewItem) {
  uploadedReferences.value = uploadedReferences.value.filter((reference) => reference.url !== item.url);
}

function isVideoFile(file: File) {
  return file.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|avi|mkv)$/i.test(file.name);
}

function mergeAgentVideoAsset(asset: AgentVideoAsset) {
  if (!asset.videoId) return;
  const index = agentVideos.value.findIndex((item) => item.videoId === asset.videoId);
  if (index < 0) {
    agentVideos.value = [...agentVideos.value, asset].slice(-AGENT_VIDEO_MAX_ITEMS);
    return;
  }
  const next = [...agentVideos.value];
  next[index] = { ...next[index], ...asset };
  agentVideos.value = next;
}

function videoStatusLabel(asset: AgentVideoAsset) {
  const status = videoAnalysisStatus(asset);
  if (status === "ready") return "已解析";
  if (status === "failed") return "解析失败";
  if (status === "processing") return "解析中";
  if (status === "queued") return "排队中";
  return "待解析";
}

function videoStatusDetail(asset: AgentVideoAsset) {
  const status = videoAnalysisStatus(asset);
  if (status === "failed") return asset.analysisError || "视频解析失败，请重试";
  if (status === "processing") return "正在抽取关键帧并解析音频";
  if (status === "queued") return "已进入解析队列";
  if (status === "ready") return "已完成视频切片分析";
  return "等待视频解析队列处理";
}

function videoStatusClass(asset: AgentVideoAsset) {
  const status = videoAnalysisStatus(asset);
  if (status === "ready") return "video-media-status--ready";
  if (status === "failed") return "video-media-status--failed";
  if (status === "processing") return "video-media-status--processing";
  return "video-media-status--queued";
}

function agentPlanAttachments(plan: VideoAgentPlan) {
  return Array.isArray(plan.attachments) ? plan.attachments : [];
}

function planImageAttachments(plan: VideoAgentPlan) {
  return agentPlanAttachments(plan).filter((item) => item.kind === "image" && item.url);
}

function planVideoAttachments(plan: VideoAgentPlan) {
  return agentPlanAttachments(plan).filter((item) => item.kind === "video" && item.url);
}

function planGenerationAttachments(plan: VideoAgentPlan) {
  return agentPlanAttachments(plan).filter((item) => item.kind === "generation" && item.task_id);
}

function generationTaskIdsForPlans(plans: VideoAgentPlan[]) {
  return Array.from(new Set(
    plans.flatMap((plan) => planGenerationAttachments(plan).map((item) => String(item.task_id || "").trim()))
      .filter(Boolean),
  ));
}

function generationTaskForAttachment(attachment: VideoAgentAttachment) {
  const taskId = String(attachment.task_id || "").trim();
  return tasks.value.find((task) => task.id === taskId);
}

function generationStatus(attachment: VideoAgentAttachment) {
  return generationTaskForAttachment(attachment)?.status || "queued";
}

function generationPrompt(attachment: VideoAgentAttachment) {
  return generationTaskForAttachment(attachment)?.prompt || attachment.prompt || "";
}

function generationMeta(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return [
    task?.model || attachment.model || "hailuo-h3-max-shouweizhen",
    formatVideoDuration(task?.duration_secs || attachment.duration_secs || 5),
    task?.resolution || task?.quality || attachment.resolution || "480P",
  ].filter(Boolean).join(" · ");
}

function generationDetail(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return task ? statusDetail(task) : "正在同步生成任务状态。";
}

function generationVideoUrl(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return task ? videoUrl(task) : "";
}

function generationCoverUrl(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return task ? coverUrl(task) : "";
}

function generationCost(attachment: VideoAgentAttachment) {
  return generationTaskForAttachment(attachment)?.cost;
}

function generationDurationMs(attachment: VideoAgentAttachment) {
  return generationTaskForAttachment(attachment)?.duration_ms;
}

function generationUpdatedAt(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return task?.updated_at || task?.created_at || "";
}

function canCancelGeneration(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return Boolean(task && !TERMINAL_STATUSES.has(task.status));
}

function cancelGeneration(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  if (task) void cancelTask(task);
}

function canReconcileGeneration(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  return Boolean(task?.reconciliation_required && task.upstream_task_id);
}

function reconcileGeneration(attachment: VideoAgentAttachment) {
  const task = generationTaskForAttachment(attachment);
  if (task) void reconcileTask(task);
}

function agentGenerationTasksFromResponse(
  plans: VideoAgentPlan[],
  items: VideoGenerationTask[],
  missingIds: string[] = [],
  missingStatus: "queued" | "error" = "error",
) {
  const foundIds = new Set(items.map((item) => item.id));
  const missing = new Set(missingIds);
  const placeholders: VideoGenerationTask[] = [];
  for (const plan of plans) {
    for (const attachment of planGenerationAttachments(plan)) {
      const taskId = String(attachment.task_id || "").trim();
      if (!taskId || foundIds.has(taskId) || !missing.has(taskId)) continue;
      placeholders.push({
        id: taskId,
        status: missingStatus,
        mode: "image_to_video",
        model: attachment.model || "hailuo-h3-max-shouweizhen",
        prompt: attachment.prompt || plan.prompt,
        aspect_ratio: "adaptive",
        duration_secs: attachment.duration_secs || 5,
        quality: attachment.resolution || "480P",
        resolution: attachment.resolution || "480P",
        image_urls: planImageAttachments(plan).map((item) => item.url || "").filter(Boolean),
        conversation_id: plan.conversation_id,
        turn_id: plan.turn_id,
        progress: missingStatus === "queued" ? "正在同步生成任务状态" : "生成任务不存在或已被删除",
        error: missingStatus === "error" ? "生成任务不存在或已被删除" : "",
        created_at: plan.created_at,
        updated_at: plan.created_at,
      });
    }
  }
  return [...items, ...placeholders];
}

async function refreshAgentGenerationTasks(plans: VideoAgentPlan[], conversationId: string) {
  const generationIds = generationTaskIdsForPlans(plans);
  if (!generationIds.length) {
    if (activeConversationId.value === conversationId) tasks.value = [];
    return;
  }
  if (activeConversationId.value === conversationId) {
    const requestedIds = new Set(generationIds);
    const currentItems = tasks.value.filter((item) => requestedIds.has(item.id));
    const currentIds = new Set(currentItems.map((item) => item.id));
    tasks.value = agentGenerationTasksFromResponse(
      plans,
      currentItems,
      generationIds.filter((id) => !currentIds.has(id)),
      "queued",
    );
  }
  await taskPolling.refresh();
}

function buildPlanAttachments(images: ReferenceUploadItem[], videos: AgentVideoAsset[]): VideoAgentAttachment[] {
  return [
    ...images.filter((item) => item.url).map((item) => ({
      kind: "image" as const,
      name: item.filename || "图片",
      mime_type: item.mime_type || "image/jpeg",
      url: item.url,
      size: item.file_size || 0,
      sha256: item.sha256 || "",
    })),
    ...videos.filter((item) => item.videoId && item.url).map((item) => ({
      kind: "video" as const,
      name: item.name || item.filename || "视频",
      mime_type: item.mimeType || item.type || "video/mp4",
      url: item.url,
      size: item.size || item.fileSize || 0,
      sha256: item.sha256 || "",
      video_id: item.videoId,
    })),
  ];
}

function scheduleAgentVideoPolling(delay = VIDEO_ANALYSIS_POLL_INTERVAL_MS) {
  if (videoAnalysisPollTimer) window.clearTimeout(videoAnalysisPollTimer);
  const ids = agentVideos.value
    .filter((asset) => VIDEO_ANALYSIS_ACTIVE_STATUSES.has(videoAnalysisStatus(asset)))
    .map((asset) => asset.videoId)
    .filter(Boolean);
  if (!isAgentWorkspace.value || !ids.length) {
    videoAnalysisPollTimer = 0;
    return;
  }
  videoAnalysisPollTimer = window.setTimeout(() => {
    videoAnalysisPollTimer = 0;
    void pollAgentVideoStatuses();
  }, Math.max(500, delay));
}

async function pollAgentVideoStatuses(extraIds: string[] = []) {
  if (!isAgentWorkspace.value || videoAnalysisPollInFlight) {
    scheduleAgentVideoPolling();
    return;
  }
  const ids = Array.from(new Set([
    ...extraIds,
    ...agentVideos.value
      .filter((asset) => VIDEO_ANALYSIS_ACTIVE_STATUSES.has(videoAnalysisStatus(asset)))
      .map((asset) => asset.videoId),
  ].filter(Boolean))).slice(0, 50);
  if (!ids.length) return;
  videoAnalysisPollInFlight = true;
  try {
    const response = await fetchAgentVideoStatuses(ids);
    const updates = new Map(response.items.filter((item) => item.videoId).map((item) => [item.videoId, item]));
    const missing = new Set(response.missing || []);
    agentVideos.value = agentVideos.value.map((asset) => {
      const update = updates.get(asset.videoId);
      if (update) return { ...asset, ...update };
      if (missing.has(asset.videoId)) {
        return { ...asset, analysisStatus: "failed", analysisError: "视频附件不存在或无权访问" };
      }
      return asset;
    });
  } catch {
    // Status polling is advisory; the attachment remains available for retry.
  } finally {
    videoAnalysisPollInFlight = false;
    scheduleAgentVideoPolling();
  }
}

async function uploadAgentVideoFiles(files: File[]) {
  if (!canUploadAgentVideos.value) {
    toast.error("当前模式不支持视频附件");
    return;
  }
  const videoFiles = files.filter(isVideoFile);
  if (!videoFiles.length) {
    toast.error("请选择 MP4、MOV、WebM、AVI 或 MKV 视频");
    return;
  }
  const remaining = AGENT_VIDEO_MAX_ITEMS - agentVideos.value.length;
  if (remaining <= 0) {
    toast.error(`单次最多上传 ${AGENT_VIDEO_MAX_ITEMS} 个视频`);
    return;
  }
  const selected = videoFiles.slice(0, remaining);
  if (selected.length < videoFiles.length) toast.error(`最多上传 ${AGENT_VIDEO_MAX_ITEMS} 个视频，已截取前 ${selected.length} 个`);
  uploadingVideos.value = true;
  try {
    const conversationId = ensureWritableConversation();
    const response = await uploadAgentVideos(selected, conversationId, true);
    response.items.forEach((asset) => mergeAgentVideoAsset(asset));
    const queueErrors = response.analysisQueueErrors || [];
    const queueFailed = queueErrors.length > 0;
    if (queueFailed) {
      toast.error(queueErrors.map((item) => item.error).join("；"));
    }
    const uploadedCount = response.items.filter((item) => item.videoId).length;
    if (!uploadedCount) throw new Error("视频上传结果不完整");
    scheduleAgentVideoPolling(250);
    if (!queueFailed) toast.success(`已上传 ${uploadedCount} 个视频，正在解析`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "上传视频失败");
  } finally {
    uploadingVideos.value = false;
  }
}

function onVideoInput(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (files.length) void uploadAgentVideoFiles(files);
}

function removeAgentVideo(asset: AgentVideoAsset) {
  agentVideos.value = agentVideos.value.filter((item) => item.videoId !== asset.videoId);
  scheduleAgentVideoPolling();
}

async function retryVideo(asset: AgentVideoAsset) {
  if (!asset.videoId || retryingVideoIds.value.has(asset.videoId)) return;
  retryingVideoIds.value = new Set([...retryingVideoIds.value, asset.videoId]);
  try {
    const response = await retryAgentVideoAnalysis(asset.videoId);
    mergeAgentVideoAsset(response.item);
    scheduleAgentVideoPolling(250);
    toast.success("视频已重新加入解析队列");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "重试视频解析失败");
  } finally {
    const next = new Set(retryingVideoIds.value);
    next.delete(asset.videoId);
    retryingVideoIds.value = next;
  }
}

function clearComposer() {
  prompt.value = "";
  uploadedReferences.value = [];
  agentVideos.value = [];
  scheduleAgentVideoPolling();
  void nextTick(() => {
    resizeTextarea();
    textarea.value?.focus();
  });
}

function createNewTask() {
  if (submitting.value) return;
  setActiveConversation(createClientConversationId());
  agentPlans.value = [];
  optimisticAgentPlans.value = [];
  if (isAgentWorkspace.value) tasks.value = [];
  if (isAgentWorkspace.value && (route.query.conversation || route.query.task)) {
    void router.replace({ name: "video-agent" });
  }
  clearComposer();
  mode.value = isAgentWorkspace.value ? "agent" : "text_to_video";
  toast.success(isAgentWorkspace.value ? "已新建智能体任务" : "已新建视频任务");
}

function selectVideoMode(value: string) {
  if (value === "agent") {
    void openAgentWorkspace();
    return;
  }
  if (!isAgentWorkspace.value && (value === "text_to_video" || value === "image_to_video")) {
    mode.value = value;
  }
}

function openAgentWorkspace() {
  void router.push({ name: "video-agent" });
}

function openGenerationWorkspace() {
  void router.push({ name: "video-generation" });
}

function syncModelOptions() {
  const spec = selectedVideoModelSpec.value;
  if (!spec) return;
  if (model.value !== spec.id && !activeVideoModelSpecs.value.some((item) => item.id === model.value)) {
    model.value = spec.id;
  }
  if (!spec.aspect_ratios.includes(aspectRatio.value)) {
    aspectRatio.value = spec.aspect_ratios[0] || "16:9";
  }
  if (!spec.durations.includes(durationSecs.value)) {
    durationSecs.value = spec.default_duration ?? spec.durations[0] ?? 5;
  }
  if (!spec.options.includes(quality.value)) {
    quality.value = spec.default_option || spec.options[0] || "";
  }
}

function selectDuration(value: string) {
  durationSecs.value = value === "auto" ? "auto" : Number(value);
}

async function submitAgentTurn(options: {
  localPlan: VideoAgentPlan;
  prompt: string;
  images: ReferenceUploadItem[];
  videos: AgentVideoAsset[];
  conversationId: string;
  reasoning: boolean;
}) {
  const { localPlan, prompt: submittedPrompt, images, videos, conversationId, reasoning } = options;
  try {
    let remotePlan: VideoAgentPlan | null = null;
    if (reasoning) {
      await streamVideoAgentPlan({
        prompt: submittedPrompt,
        conversationId,
        turnId: localPlan.turn_id,
        images,
        videos,
      }, async (event) => {
        if (event.type === "pending" || event.type === "completed") {
          remotePlan = event.message;
          return;
        }
        if (event.type === "reasoning.delta") {
          updateAgentPlan(localPlan, {
            status: "reasoning",
            reasoning_summary: `${findAgentPlan(localPlan)?.reasoning_summary || ""}${event.delta}`,
          });
        } else if (event.type === "answer.delta") {
          updateAgentPlan(localPlan, {
            status: "responding",
            message: `${findAgentPlan(localPlan)?.message || ""}${event.delta}`,
          });
        }
        await nextTick();
        if (!showScrollLatest.value) scrollLatest("auto");
      });
    } else {
      remotePlan = await createVideoAgentPlan({
        prompt: submittedPrompt,
        conversationId,
        turnId: localPlan.turn_id,
        images,
        videos,
      });
    }
    if (remotePlan) {
      if (activeConversationId.value !== conversationId) return;
      upsertAgentPlan(remotePlan);
      updateAgentPlanPolling(250);
      try {
        await refreshAgentGenerationTasks(sortedAgentPlans.value, conversationId);
      } catch {
        // The queued placeholder keeps polling without turning a saved reply into an error.
      }
      await nextTick();
      if (!showScrollLatest.value) scrollLatest("smooth");
      return;
    }
    updateAgentPlan(localPlan, { status: "analyzing" });
    updateAgentPlanPolling(250);
  } catch (error) {
    if (activeConversationId.value !== conversationId) return;
    const message = error instanceof Error ? error.message : "发送消息失败";
    updateAgentPlan(localPlan, {
      status: "failed",
      message,
      analysis_error: message,
    });
    toast.error(message);
  }
}

function findAgentPlan(plan: VideoAgentPlan) {
  return sortedAgentPlans.value.find((item) => sameAgentPlan(item, plan)) || plan;
}

async function submitTask() {
  if (!canSubmit.value) {
    if (submitDisabledReason.value) toast.error(submitDisabledReason.value);
    return;
  }
  const conversationId = ensureWritableConversation();
  if (mode.value === "agent") {
    const submittedAgentPrompt = prompt.value.trim();
    const submittedAgentImages = [...uploadedReferences.value];
    const submittedAgentVideos = [...agentVideos.value];
    const effectiveAgentPrompt = submittedAgentPrompt || "请分析我上传的图片和视频，并给出清晰、具体的结论。";
    const pendingId = `video-agent-turn-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    const hasUnreadyVideo = submittedAgentVideos.some(
      (asset) => videoAnalysisStatus(asset) !== "ready",
    );
    const localPlan: VideoAgentPlan = {
      id: pendingId,
      status: hasUnreadyVideo ? "analyzing" : reasoningEnabled.value ? "connecting" : "responding",
      prompt: effectiveAgentPrompt,
      message: "",
      reasoning_summary: "",
      reasoning_enabled: reasoningEnabled.value,
      attachments: buildPlanAttachments(submittedAgentImages, submittedAgentVideos),
      conversation_id: conversationId,
      turn_id: pendingId,
      created_at: new Date().toISOString(),
    };
    upsertAgentPlan(localPlan, true);
    clearComposer();
    void nextTick(() => scrollLatest("smooth"));
    void submitAgentTurn({
      localPlan,
      prompt: submittedAgentPrompt,
      images: submittedAgentImages,
      videos: submittedAgentVideos,
      conversationId,
      reasoning: reasoningEnabled.value,
    });
    return;
  }
  submitting.value = true;
  try {
    const generationMode = mode.value;
    const body = {
      prompt: prompt.value.trim(),
      model: selectedModelId.value,
      mode: generationMode,
      aspectRatio: aspectRatio.value,
      durationSecs: durationSecs.value,
      quality: quality.value,
      imageUrls: generationMode === "image_to_video" ? imageUrls.value : [],
      conversationId,
    };
    const fingerprint = JSON.stringify(body);
    const submissionKey = storageKey(`gmkraw:video_pending_submission:${sessionState.session?.subjectId || currentUserName.value}`);
    if (!pendingSubmission) {
      try { pendingSubmission = JSON.parse(sessionStorage.getItem(submissionKey) || "null"); } catch { /* Storage may be unavailable. */ }
    }
    if (pendingSubmission?.fingerprint !== fingerprint) pendingSubmission = { fingerprint, clientTaskId: createClientTaskId() };
    try { sessionStorage.setItem(submissionKey, JSON.stringify(pendingSubmission)); } catch { /* Keep the in-memory idempotency key. */ }
    const task = await createVideoGenerationTask({ ...body, clientTaskId: pendingSubmission.clientTaskId });
    pendingSubmission = null;
    try { sessionStorage.removeItem(submissionKey); } catch { /* The acknowledged task is already stored on the server. */ }
    if (selectedModelId.value) localStorage.setItem(VIDEO_MODEL_STORAGE_KEY, selectedModelId.value);
    mergeTask(task);
    prompt.value = "";
    toast.success("视频任务已提交");
    void nextTick(() => {
      resizeTextarea();
      scrollLatest("smooth");
    });
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "提交视频任务失败");
  } finally {
    submitting.value = false;
  }
}

async function cancelTask(task: VideoGenerationTask) {
  if (TERMINAL_STATUSES.has(task.status) || cancelingIds.value.has(task.id)) return;
  cancelingIds.value = new Set([...cancelingIds.value, task.id]);
  try {
    mergeTask(await cancelVideoGenerationTask(task.id));
    toast.success("已请求取消，上游可能仍在生成并计费");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "取消视频任务失败");
  } finally {
    const next = new Set(cancelingIds.value);
    next.delete(task.id);
    cancelingIds.value = next;
  }
}

async function reconcileTask(task: VideoGenerationTask) {
  if (!task.reconciliation_required || !task.upstream_task_id || reconcilingIds.value.has(task.id)) return;
  reconcilingIds.value = new Set([...reconcilingIds.value, task.id]);
  try {
    mergeTask(await reconcileVideoGenerationTask(task.id));
    toast.success("已重新核对原上游任务，不会重复生成");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "重新核对视频任务失败");
  } finally {
    const next = new Set(reconcilingIds.value);
    next.delete(task.id);
    reconcilingIds.value = next;
  }
}

function reuseTask(task: VideoGenerationTask) {
  prompt.value = task.prompt || "";
  model.value = task.model || model.value;
  mode.value = task.mode === "image_to_video" ? "image_to_video" : "text_to_video";
  aspectRatio.value = task.aspect_ratio || "16:9";
  durationSecs.value = task.duration_secs ?? 5;
  quality.value = task.quality || selectedVideoModelSpec.value?.default_option || "";
  syncModelOptions();
  uploadedReferences.value = (task.image_urls || []).filter(Boolean).map((url, index) => ({
    url,
    sha256: `history-${index}-${url}`,
    filename: `参考图 ${index + 1}`,
    mime_type: "",
    file_size: 0,
    cached: true,
    upload_ms: 0,
  }));
  void nextTick(() => {
    resizeTextarea();
    textarea.value?.focus();
  });
}

async function repeatTask(task: VideoGenerationTask) {
  if (task.reconciliation_required || task.cancellation_pending) {
    toast.error("上游结果和费用尚待核对，请勿重复生成");
    return;
  }
  reuseTask(task);
  await nextTick();
  await submitTask();
}

async function copyText(value: string, label: string) {
  if (!value) return;
  try {
    await navigator.clipboard?.writeText(value);
    toast.success(`${label}已复制`);
  } catch {
    toast.error("复制失败");
  }
}

watch(hasActiveAgentPlan, syncAgentFeedbackTimer, { immediate: true });
watch(hasActiveAgentPlan, () => updateAgentPlanPolling(), { immediate: true });
watch(activeConversationId, () => updateAgentPlanPolling(250));
watch(hasMessages, (active) => {
  if (active) {
    clearIdleTitleAnimation();
    idleTitleVisibleChars.value = [];
    return;
  }
  playIdleTitleAnimation();
}, { immediate: true });
watch(taskSignal, async () => {
  await nextTick();
  if (await focusRouteTask("auto")) return;
  if (!showScrollLatest.value) scrollLatest("smooth");
});
watch([() => route.query.task, () => route.query.conversation], async () => {
  if (!routeTaskId() && !routeConversationId()) return;
  lastFocusedRouteTaskId = "";
  if (routeTaskId() && await focusRouteTask("smooth", true)) return;
  void loadTasks(false);
});
watch(prompt, () => nextTick(resizeTextarea));
watch([model, videoModelSpecs, mode, imageUrls], syncModelOptions, { immediate: true });
watch(mode, (value) => {
  updateIdleTitle(value);
  if (value !== "text_to_video") return;
  uploadedReferences.value = [];
  agentVideos.value = [];
  scheduleAgentVideoPolling();
});

onMounted(() => {
  void refreshAll(true);
  void nextTick(resizeTextarea);
});

onBeforeUnmount(() => {
  disposed = true;
  loadRequestId += 1;
  if (videoAnalysisPollTimer) window.clearTimeout(videoAnalysisPollTimer);
  if (agentPlanPollTimer) window.clearTimeout(agentPlanPollTimer);
  if (agentFeedbackTimer) window.clearInterval(agentFeedbackTimer);
  if (highlightTimer) window.clearTimeout(highlightTimer);
  clearIdleTitleAnimation();
});
</script>

<template>
  <section
    class="video-chat-page bg-[#F8FAFC] px-3 py-4 dark:bg-[#0f1115] sm:px-5"
    :class="{ 'video-chat-page--agent': isAgentWorkspace }"
  >
    <div class="video-chat-shell mx-auto w-full max-w-[1120px]" :class="hasMessages ? 'is-active' : 'is-idle'">
      <div class="video-chat-content min-h-0">
        <Transition name="video-chat-content">
          <div v-if="!hasMessages" key="idle-title" class="video-empty-intro" aria-live="polite">
            <div v-if="isAgentWorkspace" class="video-agent-page-label">
              <Bot class="size-4" />
              视频智能体
            </div>
            <h1 :aria-label="idleTitle">
              <span
                v-for="(char, index) in idleTitleChars"
                :key="`${idleTitle}-${index}`"
                class="video-empty-title-char"
                :class="{ 'is-visible': idleTitleVisibleChars[index] }"
                aria-hidden="true"
              >
                {{ char === ' ' ? '\u00A0' : char }}
              </span>
            </h1>
          </div>

          <div v-else key="results" class="video-thread-panel relative min-h-0">
            <div class="video-thread-toolbar">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <h1 class="truncate text-lg font-semibold text-slate-950 dark:text-stone-50">{{ isAgentWorkspace ? '视频智能体' : '视频生成' }}</h1>
                  <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ messageCount }}</span>
                </div>
                <div v-if="isAgentWorkspace" class="mt-1 flex flex-wrap gap-2 text-[12px] font-medium text-slate-500 dark:text-stone-400">
                  {{ sortedAgentPlans.length }} 轮对话
                </div>
                <div v-else class="mt-1 flex flex-wrap gap-2 text-[12px] font-medium">
                  <span class="text-slate-500 dark:text-stone-400">运行中 {{ activeTaskCount }}</span>
                  <span class="text-slate-500 dark:text-stone-400">已完成 {{ successTaskCount }}</span>
                  <span v-if="failedTaskCount" class="text-rose-600 dark:text-rose-300">失败 {{ failedTaskCount }}</span>
                  <span v-if="configProblem" class="text-amber-700 dark:text-amber-300">{{ configProblem }}</span>
                </div>
              </div>
              <button type="button" class="studio-button inline-flex h-10 shrink-0 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-sm dark:border-white/10 dark:bg-white/[0.06]" :disabled="refreshing || loading" @click="refreshAll(false)">
                <RefreshCw class="size-4" :class="refreshing ? 'animate-spin' : ''" />
                刷新
              </button>
            </div>

            <div ref="resultsViewport" class="hide-scrollbar h-full min-h-0 overscroll-contain overflow-y-auto px-1 py-4 sm:px-4" @scroll="onResultsScroll">
              <div v-if="loading" class="grid gap-4">
                <div v-for="index in 3" :key="index" class="video-skeleton" />
              </div>

              <div v-else class="video-thread-list">
                <p v-if="taskPolling.error.value" role="status" class="text-sm text-amber-700 dark:text-amber-300">视频状态同步暂时失败，正在重试。{{ taskPolling.error.value }}</p>
                <button v-if="!isAgentWorkspace && taskNextCursor" type="button" class="studio-button mx-auto flex h-9 items-center gap-2 rounded-lg px-3 text-sm" :disabled="loadingOlderTasks || refreshing" @click="loadOlderTasks">
                  <LoaderCircle v-if="loadingOlderTasks" class="size-4 animate-spin" />
                  <ArrowUp v-else class="size-4" />
                  更早的视频任务
                </button>
                <article v-for="task in sortedTasks" :id="videoTaskDomId(task.id)" :key="task.id" class="video-turn" :class="{ 'is-highlighted': highlightedTaskId === task.id }" data-video-task-card>
                  <div class="video-message video-message--user">
                    <div class="video-avatar video-avatar--user">{{ currentUserInitial }}</div>
                    <div class="video-user-bubble">
                      <div class="mb-2 flex flex-wrap gap-1.5">
                        <span v-for="item in taskMeta(task)" :key="`${task.id}-${item}`" class="rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-semibold text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ item }}</span>
                      </div>
                      <p>{{ task.prompt }}</p>
                      <div v-if="task.image_urls?.length" class="mt-3 flex gap-2 overflow-x-auto pb-1">
                        <img v-for="url in task.image_urls" :key="url" :src="mediaUrl(url)" alt="参考图" class="size-12 shrink-0 rounded-xl object-cover" />
                      </div>
                    </div>
                  </div>

                  <div class="video-message video-message--assistant">
                    <div class="video-avatar video-avatar--assistant">
                      <Film class="size-4" />
                    </div>
                    <div class="video-assistant-bubble">
                      <div class="flex flex-wrap items-center justify-between gap-3">
                        <div class="flex flex-wrap items-center gap-2">
                          <span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold" :class="statusClass(task.status)">
                            <component :is="statusIcon(task.status)" class="size-3.5" :class="{ 'animate-spin': task.status === 'running' }" />
                            {{ statusLabel(task.status) }}
                          </span>
                          <span v-if="task.cost !== undefined" class="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ formatCost(task.cost) }}</span>
                          <span v-if="task.duration_ms" class="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ formatDuration(task.duration_ms) }}</span>
                        </div>
                        <span class="text-xs text-slate-400 dark:text-stone-500">{{ formatDate(task.updated_at || task.created_at) }}</span>
                      </div>

                      <div class="mt-3 overflow-hidden rounded-xl bg-slate-950">
                        <video v-if="task.status === 'success' && videoUrl(task)" class="aspect-video w-full bg-slate-950 object-contain" :src="mediaUrl(videoUrl(task))" :poster="mediaUrl(coverUrl(task))" controls preload="metadata" @error="videoAssets.onMediaError(task.id)" />
                        <div v-else class="grid aspect-video place-items-center px-6 text-center text-white">
                          <div>
                            <component :is="statusIcon(task.status)" class="mx-auto size-8" :class="{ 'animate-spin': task.status === 'running' }" />
                            <div class="mt-2 text-sm font-semibold">{{ statusLabel(task.status) }}</div>
                            <div class="mt-1 max-w-[360px] text-xs leading-5 text-white/65">{{ statusDetail(task) }}</div>
                            <div v-if="task.elapsed_secs" class="mt-2 text-xs text-white/50">{{ Math.round(task.elapsed_secs) }}s</div>
                          </div>
                        </div>
                      </div>

                      <p v-if="task.error" class="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300">{{ task.error }}</p>

                      <div class="mt-3 flex flex-wrap gap-2">
                        <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" @click="reuseTask(task)">
                          <Sparkles class="size-3.5" />
                          复用
                        </button>
                        <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-stone-300" :disabled="submitting || task.cancellation_pending || task.reconciliation_required" :title="task.cancellation_pending || task.reconciliation_required ? '上游结果和费用尚待核对，请勿重复生成' : undefined" @click="repeatTask(task)">
                          <RefreshCw class="size-3.5" />
                          再生成
                        </button>
                        <button v-if="task.reconciliation_required && task.upstream_task_id" type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-amber-300 bg-amber-50 px-3 text-xs font-semibold text-amber-800 disabled:cursor-wait disabled:opacity-60 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-300" :disabled="reconcilingIds.has(task.id)" title="只查询原上游任务，不会再次提交生成" @click="reconcileTask(task)">
                          <LoaderCircle v-if="reconcilingIds.has(task.id)" class="size-3.5 animate-spin" />
                          <RefreshCw v-else class="size-3.5" />
                          核对结果
                        </button>
                        <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" @click="copyText(task.id, '任务 ID')">
                          <Copy class="size-3.5" />
                          ID
                        </button>
                        <button v-if="videoUrl(task)" type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" @click="copyText(mediaUrl(videoUrl(task)), '视频链接')">
                          <Link2 class="size-3.5" />
                          链接
                        </button>
                        <button v-if="videoUrl(task)" type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="刷新视频链接" aria-label="刷新视频链接" @click="refreshVideoAsset(task.id)"><RefreshCw class="size-3.5" /></button>
                        <a v-if="videoUrl(task)" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" :href="mediaUrl(videoUrl(task))" target="_blank" rel="noreferrer">
                          <ExternalLink class="size-3.5" />
                          打开
                        </a>
                        <button v-if="!TERMINAL_STATUSES.has(task.status)" type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl bg-rose-50 px-3 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-60 dark:bg-rose-400/10 dark:text-rose-300" :disabled="cancelingIds.has(task.id)" title="请求本地取消，上游可能继续生成并计费" @click="cancelTask(task)">
                          <LoaderCircle v-if="cancelingIds.has(task.id)" class="size-3.5 animate-spin" />
                          <Trash2 v-else class="size-3.5" />
                          取消
                        </button>
                      </div>
                    </div>
                  </div>
                </article>

                <article v-for="plan in sortedAgentPlans" :key="plan.id" class="video-turn">
                  <div class="video-message video-message--user">
                    <div class="video-avatar video-avatar--user">{{ currentUserInitial }}</div>
                    <div class="video-user-bubble">
                      <p>{{ plan.prompt }}</p>
                      <div v-if="planImageAttachments(plan).length" class="mt-3 flex gap-2 overflow-x-auto pb-1">
                        <img
                          v-for="(attachment, index) in planImageAttachments(plan)"
                          :key="`${plan.id}-image-${index}-${attachment.url}`"
                          :src="mediaUrl(attachment.url)"
                          :alt="attachment.name || '上传图片'"
                          class="size-14 shrink-0 rounded-xl object-cover"
                          loading="lazy"
                          decoding="async"
                        />
                      </div>
                      <div v-if="planVideoAttachments(plan).length" class="mt-3 grid gap-2">
                        <div
                          v-for="(attachment, index) in planVideoAttachments(plan)"
                          :key="`${plan.id}-video-${index}-${attachment.video_id || attachment.url}`"
                          class="video-history-attachment"
                        >
                          <video
                            class="video-history-attachment-preview"
                            :src="mediaUrl(attachment.url)"
                            controls
                            preload="metadata"
                          />
                          <div class="video-history-attachment-name" :title="attachment.name">{{ attachment.name || '上传视频' }}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div class="video-message video-message--assistant">
                    <div class="video-avatar video-avatar--assistant">
                      <Bot class="size-4" />
                    </div>
                    <div class="video-assistant-bubble" :data-agent-status="agentFeedbackStatus(plan).id">
                      <div
                        v-if="plan.reasoning_enabled"
                        class="video-agent-reasoning-state"
                        role="status"
                        aria-live="polite"
                        :aria-label="agentFeedbackStatus(plan).label"
                        data-testid="video-agent-reasoning-progress"
                      >
                        <span class="video-agent-status-emoji" aria-hidden="true">{{ agentFeedbackStatus(plan).emoji }}</span>
                        <span class="video-agent-status-copy">
                          <strong>{{ agentFeedbackStatus(plan).label }}</strong>
                          <span>{{ agentFeedbackDetail(plan) }}</span>
                        </span>
                        <span v-if="!AGENT_TERMINAL_STATUSES.has(plan.status)" class="video-agent-dot-group" aria-hidden="true">
                          <i v-for="index in 3" :key="index" class="video-agent-dot" />
                        </span>
                      </div>
                      <div
                        v-else-if="!plan.reasoning_enabled && plan.status !== 'completed'"
                        class="video-agent-thinking"
                        role="status"
                        aria-live="polite"
                        data-testid="video-agent-thinking-state"
                        :aria-label="agentFeedbackStatus(plan).label"
                      >
                        <LoaderCircle v-if="plan.status === 'responding'" class="size-4 animate-spin" aria-hidden="true" />
                        <span v-else class="video-agent-dot-group" aria-hidden="true">
                          <i v-for="index in 3" :key="index" class="video-agent-dot" />
                        </span>
                        <span class="video-agent-status-copy">
                          <strong>{{ agentFeedbackStatus(plan).label }}</strong>
                          <span>{{ agentFeedbackDetail(plan) }}</span>
                        </span>
                      </div>
                      <details
                        v-if="plan.reasoning_enabled && (plan.reasoning_summary || ['connecting', 'reasoning', 'responding'].includes(plan.status))"
                        class="video-reasoning-panel"
                        :open="plan.status !== 'completed'"
                        data-testid="video-agent-reasoning-summary"
                      >
                        <summary class="video-reasoning-summary">
                          <span class="video-reasoning-label">思考内容</span>
                          <span v-if="plan.status !== 'completed'" class="video-reasoning-live-label">实时</span>
                          <ChevronRight v-else class="video-reasoning-chevron size-3.5" aria-hidden="true" />
                        </summary>
                        <p v-if="plan.reasoning_summary" class="video-reasoning-text" :class="{ 'is-streaming': plan.status === 'reasoning' }" aria-live="polite">{{ plan.reasoning_summary }}</p>
                        <div v-else class="video-reasoning-waiting" role="status" aria-live="polite">
                          <span>正在等待推理摘要</span>
                          <span class="video-agent-dot-group" aria-hidden="true">
                            <i v-for="index in 3" :key="index" class="video-agent-dot" />
                          </span>
                        </div>
                      </details>
                      <p v-if="plan.message && plan.status !== 'failed'" class="video-agent-reply" :class="{ 'is-streaming': plan.reasoning_enabled && plan.status === 'responding' }" aria-live="polite">{{ plan.message }}</p>
                      <section
                        v-for="attachment in planGenerationAttachments(plan)"
                        :key="`${plan.id}-generation-${attachment.task_id}`"
                        class="video-agent-generation-result"
                        data-testid="video-agent-generation-result"
                      >
                        <div class="video-agent-generation-header">
                          <div class="min-w-0">
                            <div class="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-stone-100">
                              <Film class="size-4 shrink-0 text-[#315be8] dark:text-[#9db3ff]" />
                              <span class="truncate">{{ attachment.name || '视频生成' }}</span>
                            </div>
                            <div class="mt-1 truncate text-[11px] text-slate-400 dark:text-stone-500">{{ generationMeta(attachment) }}</div>
                          </div>
                          <div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                            <span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold" :class="statusClass(generationStatus(attachment))">
                              <component :is="statusIcon(generationStatus(attachment))" class="size-3.5" :class="{ 'animate-spin': generationStatus(attachment) === 'running' }" />
                              {{ statusLabel(generationStatus(attachment)) }}
                            </span>
                            <span v-if="generationCost(attachment) !== undefined" class="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ formatCost(generationCost(attachment)) }}</span>
                          </div>
                        </div>

                        <div v-if="generationPrompt(attachment)" class="video-agent-generation-prompt">
                          <span>实际提示词</span>
                          <p>{{ generationPrompt(attachment) }}</p>
                        </div>

                        <div class="video-agent-generation-media">
                          <video
                            v-if="generationStatus(attachment) === 'success' && generationVideoUrl(attachment)"
                            class="aspect-video w-full bg-slate-950 object-contain"
                            :src="mediaUrl(generationVideoUrl(attachment))"
                            :poster="mediaUrl(generationCoverUrl(attachment))"
                            @error="videoAssets.onMediaError(String(attachment.task_id || ''))"
                            controls
                            preload="metadata"
                          />
                          <div v-else class="grid aspect-video place-items-center px-6 text-center text-white">
                            <div>
                              <component :is="statusIcon(generationStatus(attachment))" class="mx-auto size-8" :class="{ 'animate-spin': generationStatus(attachment) === 'running' }" />
                              <div class="mt-2 text-sm font-semibold">{{ statusLabel(generationStatus(attachment)) }}</div>
                              <div class="mt-1 max-w-[360px] text-xs leading-5 text-white/65">{{ generationDetail(attachment) }}</div>
                            </div>
                          </div>
                        </div>

                        <div class="video-agent-generation-footer">
                          <div class="flex flex-wrap items-center gap-2 text-[11px] text-slate-400 dark:text-stone-500">
                            <span v-if="generationDurationMs(attachment)">{{ formatDuration(generationDurationMs(attachment)) }}</span>
                            <span v-if="generationUpdatedAt(attachment)">{{ formatDate(generationUpdatedAt(attachment)) }}</span>
                          </div>
                          <div class="flex flex-wrap justify-end gap-2">
                            <button v-if="generationVideoUrl(attachment)" type="button" class="studio-button inline-flex size-8 items-center justify-center rounded-lg" title="刷新视频链接" aria-label="刷新视频链接" @click="refreshVideoAsset(String(attachment.task_id || ''))"><RefreshCw class="size-3.5" /></button>
                            <button v-if="generationVideoUrl(attachment)" type="button" class="studio-button inline-flex h-8 items-center gap-1.5 rounded-lg border border-black/[0.06] px-2.5 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" @click="copyText(mediaUrl(generationVideoUrl(attachment)), '视频链接')">
                              <Link2 class="size-3.5" />
                              链接
                            </button>
                            <a v-if="generationVideoUrl(attachment)" class="studio-button inline-flex h-8 items-center gap-1.5 rounded-lg border border-black/[0.06] px-2.5 text-xs font-semibold text-slate-600 dark:border-white/10 dark:text-stone-300" :href="mediaUrl(generationVideoUrl(attachment))" target="_blank" rel="noreferrer">
                              <ExternalLink class="size-3.5" />
                              打开
                            </a>
                            <button v-if="canReconcileGeneration(attachment)" type="button" class="studio-button inline-flex h-8 items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-2.5 text-xs font-semibold text-amber-800 disabled:cursor-wait disabled:opacity-60 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-300" :disabled="reconcilingIds.has(String(attachment.task_id || ''))" title="只查询原上游任务，不会再次提交生成" @click="reconcileGeneration(attachment)">
                              <LoaderCircle v-if="reconcilingIds.has(String(attachment.task_id || ''))" class="size-3.5 animate-spin" />
                              <RefreshCw v-else class="size-3.5" />
                              核对
                            </button>
                            <button v-if="canCancelGeneration(attachment)" type="button" class="studio-button inline-flex h-8 items-center gap-1.5 rounded-lg bg-rose-50 px-2.5 text-xs font-semibold text-rose-700 disabled:cursor-wait disabled:opacity-60 dark:bg-rose-400/10 dark:text-rose-300" :disabled="cancelingIds.has(String(attachment.task_id || ''))" @click="cancelGeneration(attachment)">
                              <LoaderCircle v-if="cancelingIds.has(String(attachment.task_id || ''))" class="size-3.5 animate-spin" />
                              <X v-else class="size-3.5" />
                              取消
                            </button>
                          </div>
                        </div>
                      </section>
                      <p v-if="plan.status === 'failed'" class="video-agent-error" role="alert">{{ plan.analysis_error || plan.message || '视频智能体处理失败，请重试' }}</p>
                      <div v-if="plan.status === 'completed'" class="mt-2 text-right text-[11px] text-slate-400 dark:text-stone-500">{{ formatDate(plan.created_at) }}</div>
                    </div>
                  </div>
                </article>
              </div>
            </div>

            <button v-if="showScrollLatest" type="button" class="studio-button absolute bottom-4 left-1/2 z-20 inline-flex size-11 -translate-x-1/2 items-center justify-center rounded-xl border border-black/[0.06] bg-white text-slate-700 shadow-[0_18px_44px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-stone-800/95 dark:text-stone-100" aria-label="滚动到最新视频任务" @click="scrollLatest()">
              <ArrowDown class="size-5" />
            </button>
          </div>
        </Transition>
      </div>

      <div class="video-chat-composer">
        <section
          class="video-composer-shell"
          :class="{ 'is-focused': isFocused, 'is-dragging': isDragging && canDropMedia, 'is-submitting': submitting }"
          @dragenter.prevent="canDropMedia && (isDragging = true)"
          @dragover.prevent="canDropMedia && (isDragging = true)"
          @dragleave.self="isDragging = false"
          @drop="onDrop"
        >
          <input
            v-if="canUploadImages"
            ref="referenceInput"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
            multiple
            class="hidden"
            data-testid="video-agent-image-input"
            @change="onReferenceInput"
          />
          <input
            v-if="canUploadAgentVideos"
            ref="videoInput"
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-m4v,video/x-msvideo,video/x-matroska"
            multiple
            class="hidden"
            data-testid="video-agent-video-input"
            @change="onVideoInput"
          />

          <div v-if="referenceItems.length || agentVideoItems.length" class="video-reference-strip">
            <div v-for="item in referenceItems" :key="item.id" class="group video-reference-thumb">
              <img :src="mediaUrl(item.url)" :alt="item.role || '参考图'" class="h-full w-full rounded-xl object-cover" />
              <button type="button" class="absolute right-1 top-1 inline-flex size-6 items-center justify-center rounded-lg bg-slate-950/75 text-white opacity-100 sm:opacity-0 sm:group-hover:opacity-100" :aria-label="`移除${item.role || '参考图'}`" @click="removeReferenceItem(item)">
                <X class="size-3" />
              </button>
              <div class="pointer-events-none absolute inset-x-1 bottom-1 rounded-lg bg-slate-950/65 px-1.5 py-1 text-[10px] font-medium leading-none text-white/90" :title="`${item.label} · ${item.meta}`">
                <div class="truncate">{{ item.label }}</div>
              </div>
            </div>
            <div v-for="item in agentVideoItems" :key="item.id" class="group video-agent-video-thumb">
              <video class="h-full w-full rounded-xl object-cover" :src="mediaUrl(item.asset.url)" preload="metadata" muted playsinline />
              <button type="button" class="absolute right-1 top-1 inline-flex size-6 items-center justify-center rounded-lg bg-slate-950/75 text-white opacity-100 sm:opacity-0 sm:group-hover:opacity-100" :aria-label="`移除视频 ${item.asset.name}`" @click="removeAgentVideo(item.asset)">
                <X class="size-3" />
              </button>
              <div class="pointer-events-none absolute inset-x-1 bottom-1 rounded-lg bg-slate-950/75 px-1.5 py-1 text-[10px] font-medium leading-tight text-white/90">
                <div class="truncate">{{ item.asset.name }}</div>
                <span class="video-media-status" :class="videoStatusClass(item.asset)" :title="videoStatusDetail(item.asset)">{{ videoStatusLabel(item.asset) }}</span>
              </div>
              <button
                v-if="videoAnalysisStatus(item.asset) === 'failed'"
                type="button"
                class="video-agent-video-retry"
                :disabled="retryingVideoIds.has(item.asset.videoId)"
                :aria-label="`重试视频解析 ${item.asset.name}`"
                title="重试视频解析"
                @click="retryVideo(item.asset)"
              >
                <LoaderCircle v-if="retryingVideoIds.has(item.asset.videoId)" class="size-3 animate-spin" />
                <RefreshCw v-else class="size-3" />
              </button>
            </div>
          </div>

          <div class="video-settings-bar">
            <div class="video-mode-navigation">
              <template v-if="!isAgentWorkspace">
                <EngineSwitch :value="mode" :options="videoModeOptions" aria-label="视频生成模式" @change="selectVideoMode" />
                <button
                  type="button"
                  class="video-agent-entry"
                  data-testid="video-agent-entry"
                  aria-label="进入视频智能体"
                  @click="openAgentWorkspace"
                >
                  <Bot class="size-4" />
                  <span>智能体</span>
                  <ChevronRight class="size-3.5" />
                </button>
              </template>
              <template v-else>
                <div class="video-agent-current" aria-current="page">
                  <Bot class="size-4" />
                  <span>视频智能体</span>
                </div>
                <button
                  type="button"
                  class="video-generation-entry"
                  data-testid="video-generation-entry"
                  aria-label="返回文生和图生视频"
                  @click="openGenerationWorkspace"
                >
                  <ArrowLeft class="size-4" />
                  <span>文生 / 图生</span>
                </button>
              </template>
            </div>

            <ComposerSelect
              v-if="showModelControls"
              v-model="model"
              class="video-model-select"
              label="模型"
              aria-label="选择视频模型"
              variant="wide"
              :options="modelSelectOptions"
            />

            <div v-if="showModelControls" class="video-chip-group">
              <ComposerSelect
                v-model="aspectRatio"
                class="video-select-control"
                label="画幅"
                aria-label="选择视频画幅"
                :options="aspectSelectOptions"
              />
              <ComposerSelect
                :model-value="String(durationSecs)"
                class="video-select-control"
                label="时长"
                aria-label="选择视频时长"
                :options="durationSelectOptions"
                @update:model-value="selectDuration"
              />
              <ComposerSelect
                v-model="quality"
                class="video-select-control"
                :label="modelOptionLabel"
                :aria-label="`选择${modelOptionLabel}`"
                :options="qualityOptions"
              />
            </div>
            <div v-else-if="mode === 'image_to_video'" class="video-mode-notice">
              图生模型待配置
            </div>
          </div>

          <div class="video-input-panel" :class="{ 'is-agent': isAgentWorkspace }">
            <textarea
              ref="textarea"
              v-model="prompt"
              class="video-main-textarea"
              :placeholder="composerPlaceholder"
              @paste="onPaste"
              @keydown.enter.exact.prevent="submitTask"
              @input="resizeTextarea"
              @focus="isFocused = true"
              @blur="isFocused = false"
            />
            <div v-if="isAgentWorkspace" class="video-reasoning-control">
              <button
                type="button"
                class="video-reasoning-toggle"
                :class="{ 'is-active': reasoningEnabled }"
                :disabled="submitting"
                :aria-pressed="reasoningEnabled"
                :aria-describedby="reasoningEnabled ? 'video-reasoning-tooltip-on' : 'video-reasoning-tooltip-off'"
                :title="reasoningEnabled ? '关闭思考模式' : '获取更智能的回答'"
                :aria-label="reasoningEnabled ? '关闭思考模式' : '开启思考模式'"
                data-testid="video-agent-reasoning-toggle"
                @click="reasoningEnabled = !reasoningEnabled"
              >
                <BrainCircuit class="size-[18px]" aria-hidden="true" />
                <span>思考</span>
              </button>
              <span
                :id="reasoningEnabled ? 'video-reasoning-tooltip-on' : 'video-reasoning-tooltip-off'"
                class="video-reasoning-tooltip"
                role="tooltip"
              >{{ reasoningEnabled ? '已开启思考模式' : '获取更智能的回答' }}</span>
            </div>
            <button type="button" class="video-send-button" :disabled="!canSubmit" :title="submitTitle" :aria-label="submitTitle" @click="submitTask">
              <LoaderCircle v-if="submitting && mode !== 'agent'" class="size-4 animate-spin" />
              <ArrowUp v-else class="size-4" />
            </button>
          </div>

          <div class="video-bottom-bar">
            <div class="video-tool-group">
              <button type="button" class="video-tool-button" :disabled="submitting" @click="createNewTask">
                <MessageSquarePlus class="size-4" />
                新建
              </button>
              <button
                v-if="canUploadImages"
                type="button"
                class="video-tool-button"
                :disabled="uploadingReferences || composerImageUrls.length >= referenceImageMaximum"
                :title="referenceUploadTitle"
                data-testid="video-agent-image-upload"
                @click="pickReferenceImages"
              >
                <LoaderCircle v-if="uploadingReferences" class="size-4 animate-spin" />
                <UploadCloud v-else class="size-4" />
                {{ referenceUploadLabel }}
                <span class="rounded-md bg-[#4F7CFF]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[#315be8] dark:text-[#9db3ff]">{{ composerImageUrls.length }}/{{ referenceImageMaximum }}</span>
              </button>
              <button
                v-if="canUploadAgentVideos"
                type="button"
                class="video-tool-button"
                :disabled="uploadingVideos || agentVideos.length >= AGENT_VIDEO_MAX_ITEMS"
                :title="`最多 ${AGENT_VIDEO_MAX_ITEMS} 个视频`"
                data-testid="video-agent-video-upload"
                @click="pickAgentVideos"
              >
                <LoaderCircle v-if="uploadingVideos" class="size-4 animate-spin" />
                <UploadCloud v-else class="size-4" />
                视频
                <span class="rounded-md bg-[#4F7CFF]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[#315be8] dark:text-[#9db3ff]">{{ agentVideos.length }}/{{ AGENT_VIDEO_MAX_ITEMS }}</span>
              </button>
            </div>
            <div class="video-composer-meta">
              <span>{{ composerSummary }}</span>
              <span v-if="submitDisabledReason">{{ submitDisabledReason }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </section>

</template>

<style scoped>
.video-chat-page {
  height: calc(100dvh - var(--studio-nav-height));
  min-height: 0;
  overflow: hidden;
}

.video-chat-shell {
  display: grid;
  height: 100%;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr) auto minmax(0, 1fr);
  row-gap: 22px;
  transition:
    grid-template-rows 420ms cubic-bezier(0.16, 1, 0.3, 1),
    row-gap 320ms ease;
}

.video-chat-shell.is-active {
  grid-template-rows: minmax(0, 1fr) auto 0px;
  row-gap: 14px;
}

.video-chat-content {
  align-self: end;
}

.video-chat-shell.is-active .video-chat-content {
  align-self: stretch;
}

.video-empty-intro {
  display: grid;
  place-items: center;
  width: min(640px, 100%);
  min-height: 48px;
  margin: 0 auto;
  padding: 0 16px 4px;
  text-align: center;
}

.video-agent-page-label {
  display: inline-flex;
  min-height: 32px;
  align-items: center;
  gap: 7px;
  margin-bottom: 12px;
  border-radius: 10px;
  background: rgba(79, 124, 255, 0.1);
  padding: 0 10px;
  color: rgb(49 91 232);
  font-size: 12px;
  font-weight: 700;
}

.dark .video-agent-page-label {
  background: rgba(79, 124, 255, 0.16);
  color: rgb(191 219 254);
}

.video-empty-intro h1 {
  margin: 0;
  color: rgb(15 23 42);
  font-size: 1.875rem;
  font-weight: 650;
  line-height: 1.25;
  letter-spacing: 0;
  text-wrap: balance;
}

.dark .video-empty-intro h1 {
  color: rgb(248 250 252);
}

.video-empty-title-char {
  display: inline-block;
  opacity: 0;
  transform: translateY(8px);
  transition:
    opacity 0.4s ease,
    transform 0.4s ease;
  will-change: opacity, transform;
}

.video-empty-title-char.is-visible {
  opacity: 1;
  transform: translateY(0);
}

.video-thread-panel {
  display: grid;
  height: 100%;
  min-height: 0;
  grid-template-rows: auto minmax(0, 1fr);
  border-radius: 12px;
  background: rgb(248 250 252);
}

.dark .video-thread-panel {
  background: rgb(17 19 23);
}

.video-thread-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 4px 0;
}

.video-thread-list {
  display: grid;
  gap: 22px;
  padding-bottom: 8px;
}

.video-turn {
  display: grid;
  gap: 12px;
  scroll-margin: 16px;
}

.video-message {
  display: flex;
  min-width: 0;
  gap: 10px;
}

.video-message--user {
  justify-content: flex-end;
}

.video-message--assistant {
  justify-content: flex-start;
}

.video-avatar {
  display: inline-grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 700;
}

.video-avatar--user {
  order: 2;
  background: rgb(15 23 42);
  color: white;
}

.dark .video-avatar--user {
  background: white;
  color: rgb(15 23 42);
}

.video-avatar--assistant {
  background: rgba(79, 124, 255, 0.12);
  color: rgb(49 91 232);
}

.dark .video-avatar--assistant {
  color: rgb(157 179 255);
}

.video-user-bubble,
.video-assistant-bubble {
  max-width: min(760px, calc(100% - 44px));
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 16px;
  transition:
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.video-turn.is-highlighted .video-user-bubble,
.video-turn.is-highlighted .video-assistant-bubble {
  border-color: rgba(79, 124, 255, 0.38);
  box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.12);
}

.video-user-bubble {
  background: rgb(238 244 255);
  padding: 12px 14px;
  color: rgb(15 23 42);
}

.video-user-bubble p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.65;
}

.video-assistant-bubble {
  width: min(760px, calc(100% - 44px));
  background: white;
  padding: 14px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
}

.dark .video-user-bubble {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(79, 124, 255, 0.16);
  color: rgb(248 250 252);
}

.dark .video-assistant-bubble {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgb(23 26 33);
  box-shadow: none;
}

.video-agent-reply {
  margin: 0;
  color: rgb(51 65 85);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.video-agent-error {
  margin: 0;
  border-radius: 8px;
  background: rgb(255 241 242);
  padding: 10px 12px;
  color: rgb(190 18 60);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.dark .video-agent-error {
  background: rgba(244, 63, 94, 0.12);
  color: rgb(253 164 175);
}

.dark .video-agent-reply {
  color: rgb(231 229 228);
}

.video-agent-generation-result {
  margin-top: 14px;
  border-top: 1px solid rgb(226 232 240);
  padding-top: 14px;
}

.video-agent-generation-header,
.video-agent-generation-footer {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.video-agent-generation-prompt {
  margin-top: 12px;
  border-left: 2px solid rgba(79, 124, 255, 0.42);
  padding-left: 10px;
}

.video-agent-generation-prompt > span {
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 700;
}

.video-agent-generation-prompt > p {
  margin-top: 3px;
  color: rgb(71 85 105);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.video-agent-generation-media {
  margin-top: 12px;
  overflow: hidden;
  border-radius: 8px;
  background: rgb(2 6 23);
}

.video-agent-generation-footer {
  margin-top: 10px;
}

.dark .video-agent-generation-result {
  border-top-color: rgba(255, 255, 255, 0.1);
}

.dark .video-agent-generation-prompt > span {
  color: rgb(168 162 158);
}

.dark .video-agent-generation-prompt > p {
  color: rgb(214 211 209);
}

.video-agent-reasoning-state {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  margin: -2px 0 12px;
  border-bottom: 1px solid rgb(226 232 240);
  padding-bottom: 12px;
}

.video-agent-status-emoji {
  display: inline-grid;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 10px;
  background: rgba(79, 124, 255, 0.1);
  font-size: 16px;
  line-height: 1;
}

.video-assistant-bubble[data-agent-status="completed"] .video-agent-status-emoji {
  background: rgba(16, 185, 129, 0.12);
}

.video-assistant-bubble[data-agent-status="failed"] .video-agent-status-emoji {
  background: rgba(244, 63, 94, 0.1);
}

.video-assistant-bubble[data-agent-status="canceled"] .video-agent-status-emoji {
  background: rgb(241 245 249);
}

.video-agent-status-copy {
  display: grid;
  min-width: 0;
  flex: 1 1 auto;
  gap: 2px;
}

.video-agent-status-copy strong {
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-agent-status-copy > span {
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 11px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-agent-dot-group {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 3px;
  color: rgb(49 91 232);
}

.video-agent-dot {
  display: block;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.3;
  animation: video-agent-dot 1.2s ease-in-out infinite;
}

.video-agent-dot:nth-child(2) {
  animation-delay: 140ms;
}

.video-agent-dot:nth-child(3) {
  animation-delay: 280ms;
}

.video-reasoning-panel {
  margin: -2px 0 12px;
  border-bottom: 1px solid rgb(226 232 240);
  padding-bottom: 12px;
}

.video-reasoning-summary {
  display: flex;
  min-height: 30px;
  cursor: pointer;
  list-style: none;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: rgb(71 85 105);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  outline: none;
  transition: color 160ms ease;
}

.video-reasoning-summary::-webkit-details-marker {
  display: none;
}

.video-reasoning-label {
  display: inline-block;
  min-width: 0;
  flex: 0 0 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-reasoning-summary:hover,
.video-reasoning-summary:focus-visible {
  color: rgb(30 41 59);
}

.video-reasoning-summary:focus-visible {
  border-radius: 7px;
  box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.16);
}

.video-reasoning-live-label {
  margin-left: auto;
  border-radius: 6px;
  background: rgba(79, 124, 255, 0.1);
  padding: 3px 6px;
  color: rgb(49 91 232);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
}

.video-reasoning-chevron {
  transition: transform 160ms ease;
}

.video-reasoning-panel[open] .video-reasoning-chevron {
  transform: rotate(90deg);
}

.video-reasoning-text,
.video-reasoning-waiting {
  margin: 9px 0 0;
  max-width: 70ch;
  color: rgb(71 85 105);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.video-reasoning-text {
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.video-reasoning-text.is-streaming::after,
.video-agent-reply.is-streaming::after {
  display: inline-block;
  width: 2px;
  height: 1.05em;
  margin-left: 4px;
  vertical-align: -0.16em;
  background: currentColor;
  content: "";
  animation: video-agent-caret 850ms steps(1, end) infinite;
}

.video-reasoning-waiting {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: rgb(100 116 139);
}

.dark .video-reasoning-panel {
  border-bottom-color: rgba(255, 255, 255, 0.1);
}

.dark .video-reasoning-summary,
.dark .video-reasoning-text {
  color: rgb(214 211 209);
}

.dark .video-reasoning-summary:hover,
.dark .video-reasoning-summary:focus-visible {
  color: rgb(250 250 249);
}

.dark .video-reasoning-waiting {
  color: rgb(168 162 158);
}

.dark .video-reasoning-label svg,
.dark .video-reasoning-summary > svg {
  color: rgb(157 179 255);
}

.video-agent-thinking {
  display: flex;
  min-width: 0;
  min-height: 36px;
  align-items: center;
  gap: 10px;
  color: rgb(49 91 232);
  line-height: 1.35;
}

.dark .video-agent-thinking {
  color: rgb(214 211 209);
}

.dark .video-agent-reasoning-state,
.dark .video-reasoning-panel {
  border-bottom-color: rgba(255, 255, 255, 0.1);
}

.dark .video-agent-status-emoji {
  background: rgba(79, 124, 255, 0.16);
}

.dark .video-assistant-bubble[data-agent-status="completed"] .video-agent-status-emoji {
  background: rgba(52, 211, 153, 0.14);
}

.dark .video-assistant-bubble[data-agent-status="failed"] .video-agent-status-emoji {
  background: rgba(251, 113, 133, 0.14);
}

.dark .video-assistant-bubble[data-agent-status="canceled"] .video-agent-status-emoji {
  background: rgba(255, 255, 255, 0.08);
}

.dark .video-agent-status-copy strong {
  color: rgb(245 245 244);
}

.dark .video-agent-status-copy > span {
  color: rgb(168 162 158);
}

.dark .video-agent-dot-group {
  color: rgb(157 179 255);
}

.dark .video-reasoning-live-label {
  background: rgba(79, 124, 255, 0.18);
  color: rgb(157 179 255);
}

.dark .video-turn.is-highlighted .video-user-bubble,
.dark .video-turn.is-highlighted .video-assistant-bubble {
  border-color: rgba(157, 179, 255, 0.45);
  box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.18);
}

.video-skeleton {
  height: 220px;
  border-radius: 16px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.12), rgba(148, 163, 184, 0.24), rgba(148, 163, 184, 0.12));
  background-size: 220% 100%;
  animation: video-skeleton 1.4s ease-in-out infinite;
}

.video-chat-composer {
  position: relative;
  z-index: 10;
  width: 100%;
  justify-self: center;
}

.video-composer-shell {
  width: 100%;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.96);
  padding: 10px;
  box-shadow: 0 16px 42px rgba(15, 23, 42, 0.08);
  transition:
    border-color 180ms ease,
    box-shadow 180ms ease,
    transform 180ms ease;
}

.dark .video-composer-shell {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgba(23, 26, 33, 0.96);
}

.video-composer-shell.is-focused,
.video-composer-shell.is-dragging {
  border-color: rgba(79, 124, 255, 0.45);
  box-shadow: 0 18px 48px rgba(79, 124, 255, 0.14);
}

.video-reference-strip {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 0 2px 10px;
}

.video-reference-thumb {
  position: relative;
  width: 64px;
  height: 64px;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 12px;
  background: rgb(241 245 249);
}

.dark .video-reference-thumb {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.06);
}

.video-agent-video-thumb {
  position: relative;
  width: 112px;
  height: 64px;
  flex: 0 0 auto;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.12);
  border-radius: 12px;
  background: rgb(15 23 42);
}

.dark .video-agent-video-thumb {
  border-color: rgba(255, 255, 255, 0.12);
}

.video-media-status {
  display: inline-block;
  max-width: 100%;
  margin-top: 2px;
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
}

.video-media-status--ready {
  background: rgba(52, 211, 153, 0.86);
  color: rgb(6 78 59);
}

.video-media-status--failed {
  background: rgba(251, 113, 133, 0.92);
  color: rgb(76 5 25);
}

.video-media-status--processing {
  background: rgba(147, 197, 253, 0.9);
  color: rgb(30 64 175);
}

.video-media-status--queued {
  background: rgba(253, 230, 138, 0.92);
  color: rgb(120 53 15);
}

.video-agent-video-retry {
  position: absolute;
  right: 4px;
  bottom: 4px;
  display: inline-grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 7px;
  background: rgb(15 23 42 / 0.82);
  color: white;
  transition: background 160ms ease, transform 160ms ease;
}

.video-agent-video-retry:hover:not(:disabled),
.video-agent-video-retry:focus-visible {
  background: rgb(49 91 232);
  outline: none;
  transform: translateY(-1px);
}

.video-agent-video-retry:disabled {
  cursor: wait;
  opacity: 0.7;
}

.video-history-attachment {
  display: grid;
  width: min(320px, 100%);
  gap: 5px;
  overflow: hidden;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.08);
  padding: 5px;
}

.video-history-attachment-preview {
  display: block;
  width: 100%;
  max-height: 220px;
  border-radius: 7px;
  background: rgb(15 23 42);
  object-fit: contain;
}

.video-history-attachment-name {
  overflow: hidden;
  padding: 0 3px 2px;
  color: rgb(71 85 105);
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dark .video-history-attachment {
  background: rgba(255, 255, 255, 0.08);
}

.dark .video-history-attachment-name {
  color: rgb(214 211 209);
}

.video-settings-bar {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-bottom: 8px;
}

.video-mode-navigation {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.video-agent-entry,
.video-generation-entry,
.video-agent-current {
  display: inline-flex;
  min-height: 44px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 12px;
  padding: 0 12px;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 700;
}

.video-agent-entry,
.video-generation-entry {
  border: 1px solid rgba(79, 124, 255, 0.2);
  background: white;
  color: rgb(49 91 232);
  transition:
    border-color 160ms ease,
    background 160ms ease,
    color 160ms ease;
}

.video-agent-entry:hover,
.video-agent-entry:focus-visible,
.video-generation-entry:hover,
.video-generation-entry:focus-visible {
  border-color: rgba(79, 124, 255, 0.42);
  background: rgba(79, 124, 255, 0.07);
  outline: none;
}

.video-agent-entry:focus-visible,
.video-generation-entry:focus-visible {
  box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.16);
}

.video-agent-current {
  background: rgb(49 91 232);
  color: white;
}

.dark .video-agent-entry,
.dark .video-generation-entry {
  border-color: rgba(157, 179, 255, 0.24);
  background: rgba(255, 255, 255, 0.06);
  color: rgb(191 219 254);
}

.dark .video-agent-entry:hover,
.dark .video-agent-entry:focus-visible,
.dark .video-generation-entry:hover,
.dark .video-generation-entry:focus-visible {
  border-color: rgba(157, 179, 255, 0.45);
  background: rgba(79, 124, 255, 0.14);
}

.video-mode-notice {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  border: 1px solid rgba(79, 124, 255, 0.2);
  border-radius: 14px;
  background: rgba(79, 124, 255, 0.06);
  padding: 0 12px;
  color: rgb(49 91 232);
  font-size: 12px;
  font-weight: 650;
}

.dark .video-mode-notice {
  border-color: rgba(157, 179, 255, 0.22);
  background: rgba(79, 124, 255, 0.12);
  color: rgb(191 219 254);
}


.video-model-select {
  min-width: 220px;
  flex: 1 1 260px;
}

.video-chip-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.video-select-control {
  min-width: 100px;
}

.video-input-panel {
  display: grid;
  min-height: 62px;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 6px;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 18px;
  background: rgb(248 250 252);
  padding: 10px 10px 10px 14px;
}

.video-input-panel.is-agent {
  grid-template-columns: minmax(0, 1fr) auto auto;
}

.dark .video-input-panel {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
}

.video-main-textarea {
  min-height: 54px;
  max-height: 180px;
  resize: none;
  border: 0;
  background: transparent;
  color: rgb(15 23 42);
  font-size: 15px;
  line-height: 1.65;
  outline: none;
}

.video-main-textarea::placeholder {
  color: rgb(100 116 139);
}

.dark .video-main-textarea {
  color: rgb(250 250 249);
}

.dark .video-main-textarea::placeholder {
  color: rgb(168 162 158);
}

.video-reasoning-control {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
}

.video-reasoning-toggle {
  display: inline-grid;
  min-width: 76px;
  height: 40px;
  flex: 0 0 auto;
  grid-auto-flow: column;
  place-items: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: 14px;
  background: transparent;
  padding: 0 12px;
  color: rgb(71 85 105);
  font-size: 13px;
  font-weight: 600;
  transition:
    border-color 160ms ease,
    background 160ms ease,
    color 160ms ease,
    transform 160ms ease;
}

.video-reasoning-toggle:hover:not(:disabled) {
  background: rgb(226 232 240);
  color: rgb(30 41 59);
  transform: translateY(-1px);
}

.video-reasoning-toggle:focus-visible {
  outline: 2px solid rgba(49, 91, 232, 0.65);
  outline-offset: 2px;
}

.video-reasoning-toggle.is-active {
  border-color: rgba(49, 91, 232, 0.35);
  background: rgba(79, 124, 255, 0.12);
  color: rgb(49 91 232);
}

.video-reasoning-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.video-reasoning-tooltip {
  position: absolute;
  right: 0;
  bottom: calc(100% + 9px);
  z-index: 30;
  max-width: calc(100vw - 24px);
  transform: translateY(5px);
  border-radius: 8px;
  background: rgb(15 23 42);
  padding: 7px 10px;
  color: white;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
  opacity: 0;
  pointer-events: none;
  white-space: nowrap;
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.18);
  transition:
    opacity 140ms ease,
    transform 140ms ease;
}

.video-reasoning-tooltip::after {
  position: absolute;
  right: 17px;
  bottom: -4px;
  width: 8px;
  height: 8px;
  transform: rotate(45deg);
  background: rgb(15 23 42);
  content: "";
}

.video-reasoning-control:hover .video-reasoning-tooltip,
.video-reasoning-control:focus-within .video-reasoning-tooltip {
  transform: translateY(0);
  opacity: 1;
}

.dark .video-reasoning-toggle {
  color: rgb(168 162 158);
}

.dark .video-reasoning-toggle:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.09);
  color: rgb(245 245 244);
}

.dark .video-reasoning-toggle.is-active {
  border-color: rgba(157, 179, 255, 0.4);
  background: rgba(79, 124, 255, 0.18);
  color: rgb(157 179 255);
}

.dark .video-reasoning-tooltip {
  background: rgb(41 37 36);
  color: rgb(250 250 249);
}

.dark .video-reasoning-tooltip::after {
  background: rgb(41 37 36);
}

.video-send-button {
  display: inline-grid;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 14px;
  background: rgb(15 23 42);
  color: white;
  transition:
    opacity 160ms ease,
    transform 160ms ease,
    background 160ms ease;
}

.video-send-button:hover:not(:disabled) {
  transform: translateY(-1px);
  background: rgb(30 41 59);
}

.video-send-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.dark .video-send-button {
  background: white;
  color: rgb(15 23 42);
}

.video-bottom-bar {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 2px 0;
}

.video-tool-group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.video-tool-button {
  display: inline-flex;
  height: 34px;
  align-items: center;
  gap: 6px;
  border-radius: 11px;
  padding: 0 10px;
  color: rgb(71 85 105);
  font-size: 12px;
  font-weight: 650;
  transition:
    background 160ms ease,
    color 160ms ease,
    opacity 160ms ease;
}

.video-tool-button:hover:not(:disabled) {
  background: rgba(79, 124, 255, 0.1);
  color: rgb(49 91 232);
}

.video-tool-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.dark .video-tool-button {
  color: rgb(214 211 209);
}

.dark .video-tool-button:hover:not(:disabled) {
  color: rgb(157 179 255);
}

.video-composer-meta {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 8px;
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 600;
}

.dark .video-composer-meta {
  color: rgb(168 162 158);
}

.video-chat-content-enter-active,
.video-chat-content-leave-active {
  transition:
    opacity 220ms ease,
    transform 320ms cubic-bezier(0.16, 1, 0.3, 1);
}

.video-chat-content-enter-from,
.video-chat-content-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

@keyframes video-skeleton {
  0% {
    background-position: 100% 0;
  }
  100% {
    background-position: -100% 0;
  }
}

@keyframes video-agent-dot {
  0%,
  60%,
  100% {
    opacity: 0.28;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

@keyframes video-agent-caret {
  0%,
  45% {
    opacity: 1;
  }
  46%,
  100% {
    opacity: 0;
  }
}

@media (max-width: 760px) {
  .video-chat-page {
    padding-top: 0.75rem;
    padding-bottom: 0.75rem;
  }

  .video-chat-shell {
    grid-template-rows: minmax(0, 1fr) auto minmax(0, 0.42fr);
    row-gap: 12px;
  }

  .video-chat-shell.is-active {
    grid-template-rows: minmax(0, 1fr) auto 0px;
  }

  .video-empty-intro {
    min-height: 40px;
    padding-inline: 8px;
  }

  .video-empty-intro h1 {
    font-size: 1.375rem;
    line-height: 1.35;
  }

  .video-thread-toolbar {
    padding-inline: 0;
  }

  .video-user-bubble,
  .video-assistant-bubble {
    max-width: calc(100% - 42px);
    border-radius: 14px;
  }

  .video-assistant-bubble {
    width: calc(100% - 42px);
  }

  .video-settings-bar {
    display: grid;
    grid-template-columns: 1fr;
  }

  .video-mode-navigation {
    width: 100%;
    justify-content: space-between;
  }

  .video-chip-group {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .video-model-select,
  .video-select-control {
    width: 100%;
    min-width: 0;
  }

  .video-bottom-bar {
    align-items: flex-start;
  }

  .video-composer-meta {
    width: 100%;
  }

  .video-agent-status-copy > span {
    max-width: 24ch;
  }

  .video-agent-reasoning-state {
    padding-bottom: 10px;
  }

  .video-agent-generation-header,
  .video-agent-generation-footer {
    align-items: flex-start;
    flex-direction: column;
  }

  .video-agent-generation-footer > :last-child {
    width: 100%;
    justify-content: flex-start;
  }
}

@media (max-width: 390px) {
  .video-agent-reasoning-state,
  .video-agent-thinking {
    gap: 7px;
  }

  .video-agent-dot-group {
    gap: 2px;
  }

}

@media (prefers-reduced-motion: reduce) {
  .video-chat-shell,
  .video-composer-shell,
  .video-send-button,
  .video-reasoning-toggle,
  .video-reasoning-summary,
  .video-reasoning-chevron,
  .video-tool-button,
  .video-agent-entry,
  .video-generation-entry,
  .video-empty-title-char,
  .video-chat-content-enter-active,
  .video-chat-content-leave-active {
    transition: none;
  }

  .video-empty-title-char {
    opacity: 1;
    transform: none;
  }

  .video-skeleton {
    animation: none;
  }

  .video-agent-dot,
  .video-reasoning-text.is-streaming::after,
  .video-agent-reply.is-streaming::after {
    animation: none;
  }

}
</style>
