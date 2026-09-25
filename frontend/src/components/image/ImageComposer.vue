<script setup lang="ts">
import {
  ArrowUp,
  Brain,
  Bot,
  FolderUp,
  ImagePlus,
  LoaderCircle,
  MessageSquarePlus,
  MoreHorizontal,
  PackageCheck,
  RectangleHorizontal,
  RectangleVertical,
  Replace,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Video,
  X,
  Zap,
} from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { analyzeImagePrompt, type ImageModel, type PromptEngineMode } from "@/lib/api";
import type { AgentFolderAsset } from "@/lib/api";
import { formatImageModel, imageModelFeatures } from "@/lib/image-models";
import type { StoredAgentVideo, StoredReferenceImage } from "@/stores/image-conversations";
import EngineSwitch from "@/components/image/EngineSwitch.vue";

const props = defineProps<{
  variant?: "dock" | "home";
  imageModels: ImageModel[];
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: StoredReferenceImage[];
  agentVideos?: StoredAgentVideo[];
  batchProductImage: StoredReferenceImage | null;
  batchFolderImages: StoredReferenceImage[];
  agentFolder: AgentFolderAsset | null;
  isSubmitting: boolean;
  isUploadingAgentVideo?: boolean;
  submitPhase?: string;
  allowReferenceOnlySubmit?: boolean;
  longTermMemoryEnabled?: boolean;
  memoryCount?: number;
}>();

const emit = defineEmits<{
  submit: [];
  createDraft: [];
  referenceFiles: [files: File[]];
  videoFiles: [files: File[]];
  removeReference: [index: number];
  removeVideo: [index: number];
  pickBatchProduct: [];
  pickBatchFolder: [];
  clearBatch: [];
  openLightbox: [images: Array<{ id: string; src: string; name?: string }>, index: number];
  openMemory: [];
}>();

const isHome = computed(() => props.variant === "home");
const prompt = defineModel<string>("prompt", { required: true });
const imageCount = defineModel<string>("imageCount", { required: true });
const imageRatio = defineModel<string>("imageRatio", { required: true });
const imageTier = defineModel<string>("imageTier", { required: true });
const imageWidth = defineModel<string>("imageWidth", { required: true });
const imageHeight = defineModel<string>("imageHeight", { required: true });
const imageQuality = defineModel<string>("imageQuality", { required: true });
const imageModel = defineModel<ImageModel>("imageModel", { required: true });
const preserveSubject = defineModel<boolean>("preserveSubject", { required: true });
const promptEngineMode = defineModel<PromptEngineMode>("promptEngineMode", { required: true });

const fileInput = ref<HTMLInputElement | null>(null);
const videoInput = ref<HTMLInputElement | null>(null);
const textarea = ref<HTMLTextAreaElement | null>(null);
const settingCards = ref<HTMLElement | null>(null);
const isDragging = ref(false);
const isFocused = ref(false);
type SettingCard = "engine" | "model" | "canvas" | "count" | "more" | "preferences";
type PromptAssistAction = "suggest" | "optimize" | "enhance";
const openSettingCard = ref<SettingCard | null>(null);
const renderedSettingCard = ref<SettingCard | null>(null);
const promptAssistAction = ref<PromptAssistAction | null>(null);
const promptAssistNote = ref("");

const assistOptions: { action: PromptAssistAction; label: string; description: string }[] = [
  { action: "suggest", label: "建议", description: "根据参考图生成方向" },
  { action: "optimize", label: "优化", description: "整理现有提示词" },
  { action: "enhance", label: "润色", description: "强化细节和质感" },
];
const qualityOptions = [
  { value: "auto", label: "智能", description: "由图片模型自动平衡速度和质量" },
  { value: "low", label: "极速", description: "优先缩短生成时间" },
  { value: "medium", label: "均衡", description: "兼顾生成速度和细节" },
  { value: "high", label: "精细", description: "优先细节，允许一次原生比例重试" },
];
const aspectOptions = [
  { ratio: "1:1", tier: "1k", width: "1024", height: "1024", label: "1:1", icon: Square },
  { ratio: "2:3", tier: "1k", width: "1024", height: "1536", label: "2:3", icon: RectangleVertical },
  { ratio: "3:2", tier: "1k", width: "1536", height: "1024", label: "3:2", icon: RectangleHorizontal },
  { ratio: "3:4", tier: "1k", width: "1024", height: "1365", label: "3:4", icon: RectangleVertical },
  { ratio: "9:16", tier: "1k", width: "1088", height: "1920", label: "9:16", icon: RectangleVertical },
  { ratio: "16:9", tier: "1k", width: "1920", height: "1088", label: "16:9", icon: RectangleHorizontal },
  { ratio: "auto", tier: "auto", width: "1024", height: "1024", label: "自动", icon: Zap },
];
const countOptions = ["1", "2", "3", "4", "6", "8", "10", "12", "16", "20"];
const STANDARD_COUNT_MAX = 100;
const AGENT_COUNT_MAX = 20;
const AGENT_FOLDER_COUNT_MAX = 300;
const previewWidth = 280;
const previewMaxHeight = 340;
const videoExtensionPattern = /\.(mp4|mov|m4v|webm|avi|mkv)$/i;
const promptMinHeight = computed(() => (isHome.value ? 28 : 108));
const promptMaxHeight = computed(() => (isHome.value ? 148 : 240));

const modelLabel = computed(() => formatImageModel(imageModel.value));
const modelFeatures = computed(() => imageModelFeatures(imageModel.value));
const qualityLabel = computed(() => qualityOptions.find((item) => item.value === imageQuality.value)?.label || "自动");
const canvasLabel = computed(() => imageRatio.value === "auto" ? "自动" : imageRatio.value);
const countLabel = computed(() => `${normalizeCountValue(imageCount.value, countMaxForCurrentMode())} 张`);
const preferencesLabel = computed(() => `${canvasLabel.value} · ${countLabel.value}`);
const hasReferences = computed(() => props.referenceImages.length > 0);
const hasAgentVideos = computed(() => isAgentMode.value && Boolean(props.agentVideos?.length));
const hasAgentFolder = computed(() => Boolean(props.agentFolder?.folderId));
const hasBatch = computed(() => Boolean(props.batchProductImage || props.batchFolderImages.length || hasAgentFolder.value));
const isBatchReplaceMode = computed(() => Boolean(props.batchProductImage && props.batchFolderImages.length));
const isFolderBatchMode = computed(() => Boolean(!isAgentMode.value && !props.batchProductImage && props.batchFolderImages.length));
const isAgentMode = computed(() => promptEngineMode.value === "professional");
const maxImageCount = computed(() => countMaxForCurrentMode());
const selectedImageCount = computed(() => normalizeCountValue(imageCount.value, maxImageCount.value));
const agentFolderCountHint = computed(() => hasAgentFolder.value
  ? `最多 ${maxImageCount.value} 张，文件夹共 ${props.agentFolder?.itemCount || 0} 张`
  : `单次最多 ${maxImageCount.value} 张`);
const engineOptions = computed(() => [
  { value: "standard", label: "标准版", icon: ImagePlus, title: "标准版：提交后立即生成" },
  { value: "professional", label: "智能体", icon: Bot, title: "专业视觉智能体：支持对话、图片和文件夹分析，确认后生成" },
]);
const canSubmit = computed(() => !props.isSubmitting && (
  Boolean(prompt.value.trim())
  || isBatchReplaceMode.value
  || hasAgentFolder.value
  || hasAgentVideos.value
  || (props.allowReferenceOnlySubmit === true && hasReferences.value)
));
const canPreserve = computed(() => hasReferences.value || hasBatch.value);
const promptPlaceholder = computed(() => {
  if (isHome.value) return hasReferences.value ? "描述你希望如何修改这张参考图..." : "描述你想生成的图片...";
  return isBatchReplaceMode.value ? "补充批量替换要求..." : isFolderBatchMode.value ? "输入要套用到每张文件夹图片的生成要求..." : hasReferences.value ? "描述你希望如何修改参考图..." : "输入商品图片生成需求...";
});
const agentPromptPlaceholder = computed(() => {
  if (hasAgentVideos.value) return "描述希望从视频里提炼的商品信息、关键画面或生图方向...";
  return hasReferences.value ? "继续描述你希望怎么调整这张图..." : "输入你的商品、目标和画面要求...";
});
const submitText = computed(() => props.isSubmitting ? isAgentMode.value ? "处理中" : "提交中" : isAgentMode.value ? "发送" : isBatchReplaceMode.value ? "批量替换" : isFolderBatchMode.value ? "批量生图" : hasReferences.value ? "编辑图片" : "生成图片");
const submitStatusText = computed(() => props.submitPhase || submitText.value);
const promptShellState = computed(() => ({
  "is-active": isFocused.value || Boolean(prompt.value.trim()) || hasReferences.value || hasBatch.value,
  "is-focused": isFocused.value,
  "is-submitting": props.isSubmitting,
}));
const referencePreview = ref<{
  src: string;
  name: string;
  left: number;
  top: number;
  width: number;
} | null>(null);

function formatModel(value: string) {
  return formatImageModel(value);
}
function normalizeCountValue(value: unknown, max = STANDARD_COUNT_MAX) {
  const safeMax = Math.max(1, Math.floor(Number(max) || STANDARD_COUNT_MAX));
  return Math.min(safeMax, Math.max(1, Math.floor(Number(value) || 1)));
}
function countMaxForCurrentMode() {
  if (promptEngineMode.value !== "professional") return STANDARD_COUNT_MAX;
  const folder = props.agentFolder;
  if (!folder?.folderId) return AGENT_COUNT_MAX;
  return Math.max(1, Math.min(AGENT_FOLDER_COUNT_MAX, folder.itemCount || 1));
}
function setCount(value: string | number) {
  imageCount.value = String(normalizeCountValue(value, maxImageCount.value));
}
function clampCount(value: string) {
  imageCount.value = value === "" ? "" : String(normalizeCountValue(value, maxImageCount.value));
}
function stepCount(delta: number) {
  setCount(Math.floor(Number(imageCount.value) || 1) + delta);
}
function resizePromptTextarea() {
  const element = textarea.value;
  if (!element) return;
  element.style.height = "auto";
  const nextHeight = Math.min(Math.max(element.scrollHeight, promptMinHeight.value), promptMaxHeight.value);
  element.style.height = `${nextHeight}px`;
  element.style.overflowY = element.scrollHeight > promptMaxHeight.value ? "auto" : "hidden";
}
function setAspect(option: typeof aspectOptions[number]) {
  imageRatio.value = option.ratio;
  imageTier.value = option.tier;
  imageWidth.value = option.width;
  imageHeight.value = option.height;
}
function showReferencePreview(image: StoredReferenceImage, index: number, event: MouseEvent | FocusEvent) {
  const target = event.currentTarget as HTMLElement | null;
  const src = image.dataUrl || image.url || "";
  if (!target || !src) return;
  const rect = target.getBoundingClientRect();
  const padding = 12;
  const gap = 10;
  const width = Math.max(160, Math.min(previewWidth, window.innerWidth - padding * 2));
  const maxLeft = Math.max(padding, window.innerWidth - width - padding);
  const left = Math.min(Math.max(rect.left, padding), maxLeft);
  const belowTop = rect.bottom + gap;
  const top = belowTop + previewMaxHeight <= window.innerHeight - padding
    ? belowTop
    : Math.max(padding, rect.top - previewMaxHeight - gap);

  referencePreview.value = {
    src,
    name: image.name || `reference-${index + 1}`,
    left,
    top,
    width,
  };
}
function referenceSource(image: StoredReferenceImage) {
  return image.dataUrl || image.url || "";
}
function referenceItems(images = props.referenceImages) {
  return images
    .map((image, index) => ({ id: `composer-reference-${index}`, src: referenceSource(image), name: image.name || `reference-${index + 1}.png` }))
    .filter((item) => item.src);
}
function openReferenceLightbox(index: number) {
  const items = referenceItems();
  if (!items.length) return;
  hideReferencePreview();
  emit("openLightbox", items, Math.max(0, Math.min(index, items.length - 1)));
}
function hideReferencePreview() {
  referencePreview.value = null;
}
function toggleSettingCard(card: SettingCard) {
  if (openSettingCard.value === card) {
    openSettingCard.value = null;
    return;
  }
  renderedSettingCard.value = card;
  openSettingCard.value = card;
}
function closeSettingCards() {
  openSettingCard.value = null;
}
function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (settingCards.value?.contains(target)) return;
  openSettingCard.value = null;
}
function runPromptAssist(action: PromptAssistAction) {
  openSettingCard.value = null;
  void assist(action);
}
function selectEngine(value: string) {
  const nextMode = value as PromptEngineMode;
  if (nextMode === "professional" && hasBatch.value) {
    promptAssistNote.value = "请先清空批量素材，再切换到专业视觉智能体。";
    return;
  }
  closeSettingCards();
  promptAssistAction.value = null;
  promptAssistNote.value = "";
  promptEngineMode.value = nextMode;
  void nextTick(resizePromptTextarea);
}
function pickReferences() {
  fileInput.value?.click();
}
function pickVideos() {
  videoInput.value?.click();
}
function isImageFile(file: File) {
  return file.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|svg)$/i.test(file.name);
}
function isVideoFile(file: File) {
  return file.type.startsWith("video/") || videoExtensionPattern.test(file.name);
}
function onFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter(isImageFile);
  if (files.length) emit("referenceFiles", files);
  input.value = "";
}
function onVideoFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter(isVideoFile);
  if (files.length) emit("videoFiles", files);
  input.value = "";
}
function onPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) return;
  event.preventDefault();
  emit("referenceFiles", files);
}
function onPromptKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.isComposing) return;
  if (event.shiftKey) return;
  event.preventDefault();
  if (canSubmit.value) emit("submit");
}
function onDrop(event: DragEvent) {
  event.preventDefault();
  isDragging.value = false;
  const dropped = Array.from(event.dataTransfer?.files || []);
  const imageFiles = dropped.filter(isImageFile);
  const videoFiles = dropped.filter(isVideoFile);
  if (imageFiles.length) emit("referenceFiles", imageFiles);
  if (videoFiles.length) emit("videoFiles", videoFiles);
}
function formatVideoSize(bytes: number) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${value} B`;
}
function videoStatusLabel(video: StoredAgentVideo) {
  if (video.analysisStatus === "ready" || video.status === "ready") return "已解析";
  if (video.analysisStatus === "failed" || video.status === "failed") return "解析失败";
  if (video.analysisStatus === "queued" || video.status === "queued") return "排队中";
  if (video.analysisStatus === "processing" || video.status === "processing") return "解析中";
  return "待解析";
}
async function assist(action: PromptAssistAction) {
  if (!hasReferences.value || promptAssistAction.value) {
    promptAssistNote.value = "请先上传商品参考图，再让 AI 分析图片并生成 Prompt。";
    return;
  }
  promptAssistAction.value = action;
  promptAssistNote.value = "AI 正在分析参考图...";
  try {
    const analyzableImages = props.referenceImages
      .filter((image) => image.dataUrl)
      .slice(0, 6)
      .map((image) => ({ name: image.name, dataUrl: image.dataUrl || "" }));
    if (!analyzableImages.length) throw new Error("当前历史参考图已瘦身为 URL，请先加入本地图片后再分析");
    const result = await analyzeImagePrompt({
      action,
      mode: "single",
      prompt: prompt.value.trim(),
      images: analyzableImages,
    });
    const analysis = [
      result.analysis.subject && `主体识别：${result.analysis.subject}`,
      result.analysis.materials && `材质/细节：${result.analysis.materials}`,
      result.analysis.style && `风格判断：${result.analysis.style}`,
      result.analysis.composition && `构图光线：${result.analysis.composition}`,
      result.analysis.textLogo && `文字/Logo：${result.analysis.textLogo}`,
      result.analysis.risks && `风险提醒：${result.analysis.risks}`,
    ].filter(Boolean);
    prompt.value = action === "suggest"
      ? ["AI 图片分析：", ...analysis, "", "Prompt 建议：", ...(result.suggestions.length ? result.suggestions.map((item, index) => `${index + 1}. ${item}`) : [result.suggestionPrompt]), "", "可直接使用：", result.suggestionPrompt].join("\n")
      : ["AI 图片分析：", ...analysis, "", "优化后的 Prompt：", result.optimizedPrompt, result.negativePrompt ? `\nNegative Prompt：${result.negativePrompt}` : ""].join("\n");
    promptAssistNote.value = "图片分析已完成。";
  } catch (error) {
    promptAssistNote.value = `图片分析失败：${error instanceof Error ? error.message : "未知错误"}`;
  } finally {
    promptAssistAction.value = null;
  }
}

watch(prompt, () => {
  void nextTick(resizePromptTextarea);
}, { flush: "post" });

onMounted(() => {
  void nextTick(resizePromptTextarea);
  window.addEventListener("resize", resizePromptTextarea);
  document.addEventListener("pointerdown", onDocumentPointerDown);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizePromptTextarea);
  document.removeEventListener("pointerdown", onDocumentPointerDown);
});

watch(promptEngineMode, () => {
  closeSettingCards();
  promptAssistAction.value = null;
  promptAssistNote.value = "";
  void nextTick(resizePromptTextarea);
});
</script>

<template>
  <section
    class="composer-prompt-shell"
    :class="[
      isHome ? 'composer-prompt-shell--home w-full rounded-[30px] border border-black/[0.08] bg-white/95 p-2 shadow-[0_16px_42px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-[#171a21]/95' : 'rounded-[22px] border border-black/[0.08] bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#171a21]',
      promptShellState,
      isDragging ? 'is-dragging ring-4 ring-[#4F7CFF]/15' : '',
    ]"
    @dragenter.prevent="isDragging = true"
    @dragover.prevent="isDragging = true"
    @dragleave.self="isDragging = false"
    @drop="onDrop"
  >
      <div v-if="referenceImages.length" class="composer-reference-row">
        <div class="composer-reference-strip">
          <div
            v-for="(image, index) in referenceImages"
            :key="`${image.name}-${index}`"
            class="group relative size-16 shrink-0 rounded-xl border border-black/[0.06] outline-none ring-[#4F7CFF]/30 transition dark:border-white/10"
            data-reference-thumb
            @mouseenter="showReferencePreview(image, index, $event)"
            @mouseleave="hideReferencePreview"
          >
            <button
            type="button"
            class="block h-full w-full rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/30"
            :aria-label="`放大查看参考图 ${index + 1}`"
            @click="openReferenceLightbox(index)"
            @focusin="showReferencePreview(image, index, $event)"
            @focusout="hideReferencePreview"
            @keydown.escape.stop="hideReferencePreview"
          >
            <img :src="referenceSource(image)" alt="参考图" class="h-full w-full cursor-zoom-in rounded-xl object-cover transition duration-200 group-hover:scale-[1.03]" />
            </button>
            <button type="button" class="absolute right-1 top-1 inline-flex size-6 items-center justify-center rounded-lg bg-slate-950/75 text-white opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100" aria-label="移除参考图" @click.stop="hideReferencePreview(); emit('removeReference', index)">
              <X class="size-3" />
            </button>
          </div>
        </div>
        <div v-if="!isAgentMode" class="composer-reference-assist">
          <button
            v-for="item in assistOptions"
            :key="item.action"
            type="button"
            class="composer-reference-assist-button"
            :disabled="!hasReferences || Boolean(promptAssistAction)"
            @click="runPromptAssist(item.action)"
          >
            <Sparkles class="size-4" :class="promptAssistAction === item.action ? 'ai-orbit' : ''" />
            <span>
              <strong>{{ item.label }}</strong>
              <small>{{ item.description }}</small>
            </span>
          </button>
        </div>
      </div>

      <div v-if="hasBatch && !isAgentMode" class="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.06] px-3 py-2">
        <Replace v-if="batchProductImage" class="size-4 text-[#315be8]" />
        <FolderUp v-else class="size-4 text-[#315be8]" />
        <div class="min-w-0 flex-1 text-xs text-slate-600 dark:text-stone-300">
          <div class="font-semibold text-slate-700 dark:text-stone-100">
            {{ batchProductImage ? '批量换商品' : '文件夹批量生图' }}
          </div>
          <div class="mt-0.5">
            {{ batchProductImage ? `主图已上传，文件夹 ${batchFolderImages.length} 张；每张场景图会独立替换商品` : `已读取 ${batchFolderImages.length} 张图片；输入提示词后每张图独立生成` }}
          </div>
        </div>
        <button type="button" class="rounded-xl px-2 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50" @click="emit('clearBatch')">清空</button>
      </div>

      <div v-if="hasAgentFolder && isAgentMode" class="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.06] px-3 py-2" data-testid="agent-folder-asset">
        <FolderUp class="size-4 shrink-0 text-[#315be8]" />
        <div class="min-w-0 flex-1 text-xs text-slate-600 dark:text-stone-300">
          <div class="font-semibold text-slate-700 dark:text-stone-100">{{ props.agentFolder?.name || '已上传文件夹' }}</div>
          <div class="mt-0.5">已保存 {{ props.agentFolder?.itemCount || 0 }} 张，本次处理 {{ selectedImageCount }} 张；Agent 会先读取摘要并抽样分析</div>
        </div>
        <button type="button" class="rounded-xl px-2 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50" @click="emit('clearBatch')">清空</button>
      </div>

      <div v-if="hasAgentVideos" class="composer-video-row" data-testid="agent-video-assets">
        <div
          v-for="(video, index) in agentVideos || []"
          :key="video.videoId || `${video.name}-${index}`"
          class="composer-video-chip"
        >
          <span class="composer-video-icon">
            <Video class="size-4" />
          </span>
          <span class="composer-video-meta">
            <strong :title="video.name">{{ video.name }}</strong>
            <small :title="video.analysisError || ''">{{ formatVideoSize(video.size) }} · {{ videoStatusLabel(video) }}</small>
          </span>
          <button
            type="button"
            class="composer-video-remove"
            :aria-label="`移除视频 ${index + 1}`"
            @click="emit('removeVideo', index)"
          >
            <X class="size-3" />
          </button>
        </div>
      </div>

      <input ref="fileInput" type="file" accept="image/*" multiple class="hidden" data-testid="reference-file-input" @change="onFiles" />
      <input ref="videoInput" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-msvideo,video/x-matroska,.mp4,.mov,.m4v,.webm,.avi,.mkv" multiple class="hidden" data-testid="agent-video-file-input" @change="onVideoFiles" />

      <div ref="settingCards" class="composer-control-surface" @keydown.escape="closeSettingCards">
        <div class="composer-settings-bar" :class="{ 'is-agent-mode': isAgentMode }">
          <div class="composer-setting-group">
            <div class="composer-inline-setting composer-inline-setting--engine" data-testid="image-engine-card-toggle">
              <span class="composer-inline-label">模式</span>
              <EngineSwitch :value="promptEngineMode" :options="engineOptions" aria-label="生成模式" @change="selectEngine" />
            </div>
            <button
              v-if="!isAgentMode"
              type="button"
              class="composer-inline-setting"
              :class="{ 'is-selected': openSettingCard === 'model' }"
              :aria-expanded="openSettingCard === 'model'"
              aria-controls="composer-model-card-panel"
              data-testid="image-model-card-toggle"
              @click="toggleSettingCard('model')"
            >
              <span class="composer-inline-label">模型</span>
              <strong>{{ modelLabel }}</strong>
            </button>
            <button
              v-if="!isAgentMode"
              type="button"
              class="composer-inline-setting"
              :class="{ 'is-selected': openSettingCard === 'canvas' }"
              :aria-expanded="openSettingCard === 'canvas'"
              aria-controls="composer-canvas-card-panel"
              data-testid="image-canvas-card-toggle"
              @click="toggleSettingCard('canvas')"
            >
              <span class="composer-inline-label">画布</span>
              <strong>{{ canvasLabel }}</strong>
            </button>
            <button
              v-if="isAgentMode"
              type="button"
              class="composer-inline-setting composer-inline-setting--preferences"
              :class="{ 'is-selected': openSettingCard === 'preferences' }"
              :aria-expanded="openSettingCard === 'preferences'"
              aria-controls="composer-preferences-card-panel"
              data-testid="agent-generation-preferences-toggle"
              @click="toggleSettingCard('preferences')"
            >
              <SlidersHorizontal class="size-4" aria-hidden="true" />
              <span class="composer-inline-label">生成偏好</span>
              <strong>{{ preferencesLabel }}</strong>
            </button>
          </div>
          <div class="composer-setting-group composer-setting-group--trailing">
            <div v-if="!isAgentMode" class="composer-count-stepper" :class="{ 'is-selected': openSettingCard === 'count' }">
              <button type="button" aria-label="减少生成数量" @click="stepCount(-1)">-</button>
              <button
                type="button"
                class="composer-count-value"
                :aria-expanded="openSettingCard === 'count'"
                aria-controls="composer-count-card-panel"
                data-testid="image-count-card-toggle"
                @click="toggleSettingCard('count')"
              >
                <span>数量</span>
                <strong>{{ selectedImageCount }}</strong>
              </button>
              <button type="button" aria-label="增加生成数量" @click="stepCount(1)">+</button>
            </div>
            <button
              v-if="!isAgentMode"
              type="button"
              class="composer-more-button"
              :class="{ 'is-selected': openSettingCard === 'more' }"
              :aria-expanded="openSettingCard === 'more'"
              aria-controls="composer-more-card-panel"
              data-testid="image-more-card-toggle"
              @click="toggleSettingCard('more')"
            >
              <MoreHorizontal class="size-4" />
            </button>
          </div>
        </div>

        <div v-if="!isAgentMode" class="composer-input-panel composer-input-panel--standard" :class="{ 'is-focused': isFocused }">
          <textarea
            id="image-prompt-input"
            ref="textarea"
            v-model="prompt"
             :placeholder="promptPlaceholder"
            class="composer-main-textarea"
            :style="{ minHeight: `${promptMinHeight}px`, maxHeight: `${promptMaxHeight}px` }"
            data-testid="image-prompt-input"
            @paste="onPaste"
            @keydown="onPromptKeydown"
            @input="resizePromptTextarea"
            @focus="isFocused = true"
            @blur="isFocused = false"
          />
          <button
            type="button"
            class="composer-send-button"
            :disabled="!canSubmit"
            :title="submitStatusText"
            :aria-label="submitStatusText"
            data-testid="generate-submit-button"
            @click="emit('submit')"
          >
            <LoaderCircle v-if="isSubmitting" class="size-4 animate-spin" />
            <ArrowUp v-else class="size-4" />
            <span class="sr-only">{{ submitStatusText }}</span>
          </button>
        </div>

        <div v-else class="composer-input-panel composer-input-panel--agent" :class="{ 'is-focused': isFocused }">
          <textarea
            id="image-prompt-input"
            ref="textarea"
            v-model="prompt"
            :placeholder="agentPromptPlaceholder"
            class="composer-main-textarea"
            :style="{ minHeight: `${promptMinHeight}px`, maxHeight: `${promptMaxHeight}px` }"
            data-testid="image-prompt-input"
            data-mode-input="agent"
            @paste="onPaste"
            @keydown="onPromptKeydown"
            @input="resizePromptTextarea"
            @focus="isFocused = true"
            @blur="isFocused = false"
          />
          <button
            type="button"
            class="composer-send-button composer-send-button--agent"
            :disabled="!canSubmit"
            :title="submitStatusText"
            :aria-label="submitStatusText"
            data-testid="generate-submit-button"
            @click="emit('submit')"
          >
            <LoaderCircle v-if="isSubmitting" class="size-4 animate-spin" />
            <ArrowUp v-else class="size-4" />
            <span class="sr-only">{{ submitStatusText }}</span>
          </button>
        </div>

        <div class="composer-bottom-bar">
          <div class="composer-tool-group">
            <button type="button" class="composer-tool-button" @click="emit('createDraft')">
              <MessageSquarePlus class="size-4" />
              新建任务
            </button>
            <button
              v-if="isAgentMode"
              type="button"
              class="composer-tool-button"
              :class="props.longTermMemoryEnabled === false ? 'text-slate-400 dark:text-stone-500' : 'text-[#315be8] dark:text-[#9db3ff]'"
              :title="props.longTermMemoryEnabled === false ? '当前为临时对话' : '管理长期记忆'"
              data-testid="agent-memory-button"
              @click="emit('openMemory')"
            >
              <Brain class="size-4" />
              {{ props.longTermMemoryEnabled === false ? '临时对话' : '记忆' }}
              <span v-if="props.longTermMemoryEnabled !== false && Number(props.memoryCount || 0) > 0" class="rounded-md bg-[#4F7CFF]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[#315be8] dark:text-[#9db3ff]">{{ props.memoryCount }}</span>
            </button>
            <button type="button" class="composer-tool-button" data-testid="pick-batch-folder-button" @click="emit('pickBatchFolder')">
              <FolderUp class="size-4" />
              {{ isAgentMode ? '分析文件夹' : '批量文件夹' }}
            </button>
            <button v-if="!isAgentMode" type="button" class="composer-tool-button" data-testid="pick-batch-product-button" @click="emit('pickBatchProduct')">
              <PackageCheck class="size-4" />
              换商品主图
            </button>
            <button type="button" class="composer-tool-button" data-testid="pick-reference-button" @click="pickReferences">
              <ImagePlus class="size-4" />
              图片
            </button>
            <button
              v-if="isAgentMode"
              type="button"
              class="composer-tool-button"
              :disabled="props.isUploadingAgentVideo"
              :aria-busy="props.isUploadingAgentVideo"
              data-testid="pick-agent-video-button"
              @click="pickVideos"
            >
              <LoaderCircle v-if="props.isUploadingAgentVideo" class="size-4 animate-spin" />
              <Video v-else class="size-4" />
              {{ props.isUploadingAgentVideo ? '上传中' : '视频' }}
            </button>
          </div>
          <div class="composer-tool-group composer-tool-group--trailing">
            <span v-if="isSubmitting" class="composer-submit-status" role="status" aria-live="polite">{{ submitStatusText }}</span>
          </div>
        </div>

          <div class="composer-setting-card-panel-clip" :class="{ 'is-open': Boolean(openSettingCard) }" :aria-hidden="!openSettingCard" :inert="!openSettingCard">
            <div v-if="renderedSettingCard" class="composer-setting-card-panel mt-2 rounded-xl border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]">
              <div v-if="renderedSettingCard === 'preferences'" id="composer-preferences-card-panel" class="composer-preferences-panel">
                <div class="composer-preferences-heading">
                  <strong>生成偏好</strong>
                  <button type="button" class="composer-panel-close" aria-label="关闭生成偏好" @click="closeSettingCards">
                    <X class="size-4" />
                  </button>
                </div>
                <div class="composer-preferences-grid">
                  <label class="composer-preference-field composer-preference-field--model">
                    <span>图片模型</span>
                    <select v-model="imageModel" class="studio-input h-11 px-3 text-sm">
                      <option v-for="model in imageModels" :key="model" :value="model">{{ formatModel(model) }} - {{ model }}</option>
                    </select>
                  </label>
                  <fieldset class="composer-preference-field composer-preference-field--canvas">
                    <legend>画布比例</legend>
                    <div class="grid grid-cols-4 gap-2 sm:grid-cols-7">
                      <button v-for="option in aspectOptions" :key="`agent-${option.ratio}-${option.label}`" type="button" class="studio-button flex min-h-11 flex-col items-center justify-center gap-0.5 rounded-lg border text-xs font-medium" :class="option.ratio === imageRatio && option.tier === imageTier && option.width === imageWidth && option.height === imageHeight ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" @click="setAspect(option)">
                        <component :is="option.icon" class="size-3.5" />
                        <span>{{ option.label }}</span>
                      </button>
                    </div>
                  </fieldset>
                  <fieldset class="composer-preference-field">
                    <legend>速度 / 质量</legend>
                    <div class="grid grid-cols-4 gap-2">
                      <button v-for="option in qualityOptions" :key="`agent-quality-${option.value}`" type="button" class="studio-button min-h-11 rounded-lg border text-xs font-medium" :class="option.value === imageQuality ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" :title="option.description" @click="imageQuality = option.value">{{ option.label }}</button>
                    </div>
                  </fieldset>
                  <fieldset class="composer-preference-field">
                    <legend>生成数量</legend>
                    <div class="grid grid-cols-6 gap-2">
                      <button v-for="value in countOptions" :key="`agent-count-${value}`" type="button" class="studio-button min-h-11 rounded-lg border text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40" :class="selectedImageCount === Number(value) ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" :disabled="Number(value) > maxImageCount" @click="setCount(value)">{{ value }}</button>
                    </div>
                    <div class="mt-2 flex flex-wrap items-center gap-2">
                      <input :value="selectedImageCount" type="number" min="1" :max="maxImageCount" aria-label="自定义生成数量" class="studio-input h-10 max-w-[140px] px-3 text-center" @input="clampCount(($event.target as HTMLInputElement).value)" />
                      <span class="text-xs text-slate-400">{{ agentFolderCountHint }}</span>
                    </div>
                  </fieldset>
                </div>
              </div>

              <div v-else-if="renderedSettingCard === 'model'" id="composer-model-card-panel" class="grid gap-3 sm:grid-cols-[minmax(220px,1fr)_auto]">
                <div class="min-w-0">
                  <label class="mb-2 block text-xs font-semibold text-slate-500">模型</label>
                  <select v-model="imageModel" class="studio-input h-10 px-3 text-sm">
                    <option v-for="model in imageModels" :key="model" :value="model">{{ formatModel(model) }} - {{ model }}</option>
                  </select>
                  <div class="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500 dark:text-stone-400">
                    <span class="max-w-full truncate rounded-full bg-white px-2 py-1 font-mono dark:bg-white/[0.08]">{{ imageModel }}</span>
                    <span v-for="feature in modelFeatures" :key="feature" class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 font-medium text-[#315be8]">{{ feature }}</span>
                  </div>
                </div>
                <div class="min-w-[190px]">
                  <label class="mb-2 block text-xs font-semibold text-slate-500">速度 / 质量</label>
                  <div class="grid grid-cols-4 gap-2">
                    <button v-for="option in qualityOptions" :key="option.value" type="button" class="studio-button h-11 rounded-xl border text-[13px] font-medium" :class="option.value === imageQuality ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" :title="option.description" @click="imageQuality = option.value">{{ option.label }}</button>
                  </div>
                  <label class="mt-2 inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" :class="canPreserve ? 'cursor-pointer' : 'cursor-not-allowed opacity-55'">
                    <input v-model="preserveSubject" type="checkbox" class="size-4 accent-[#4F7CFF]" :disabled="!canPreserve" />
                    <ShieldCheck class="size-4" />
                    主体保真
                  </label>
                </div>
              </div>

              <div v-else-if="renderedSettingCard === 'canvas'" id="composer-canvas-card-panel" class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
                <div>
                  <label class="mb-2 block text-xs font-semibold text-slate-500">画布比例</label>
                  <div class="grid grid-cols-3 gap-2 sm:grid-cols-7">
                    <button v-for="option in aspectOptions" :key="`${option.ratio}-${option.tier}-${option.label}`" type="button" class="studio-button flex h-[58px] flex-col items-center justify-center gap-1 rounded-xl border text-[13px] font-medium" :class="option.ratio === imageRatio && option.tier === imageTier && option.width === imageWidth && option.height === imageHeight ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" @click="setAspect(option)">
                      <component :is="option.icon" class="size-4" />
                      <span>{{ option.label }}</span>
                    </button>
                  </div>
                </div>
                <div>
                  <label class="mb-2 block text-xs font-semibold text-slate-500">自定义尺寸</label>
                  <div class="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                    <input v-model="imageWidth" type="number" min="1" class="studio-input h-10 px-3 text-center" />
                    <span class="text-sm text-slate-400">x</span>
                    <input v-model="imageHeight" type="number" min="1" class="studio-input h-10 px-3 text-center" />
                  </div>
                </div>
              </div>

              <div v-else-if="renderedSettingCard === 'count'" id="composer-count-card-panel" class="max-w-[360px]">
                <label class="mb-2 block text-xs font-semibold text-slate-500">生成数量</label>
                <div class="grid grid-cols-3 gap-2 sm:grid-cols-6">
                  <button v-for="value in countOptions" :key="value" type="button" class="studio-button h-11 rounded-xl border text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-40" :class="selectedImageCount === Number(value) ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" :disabled="Number(value) > maxImageCount" @click="setCount(value)">{{ value }} 张</button>
                </div>
                <div class="mt-2 flex items-center gap-2">
                  <input :value="selectedImageCount" type="number" min="1" :max="maxImageCount" aria-label="自定义张数" class="studio-input h-10 max-w-[140px] px-3 text-center" @input="clampCount(($event.target as HTMLInputElement).value)" />
                  <span class="text-xs text-slate-400">张</span>
                </div>
              </div>

              <div v-else-if="renderedSettingCard === 'more'" id="composer-more-card-panel" class="grid gap-2 sm:grid-cols-3">
                <button
                  v-for="item in assistOptions"
                  :key="item.action"
                  type="button"
                  class="composer-more-action"
                  :disabled="!hasReferences || Boolean(promptAssistAction)"
                  @click="runPromptAssist(item.action)"
                >
                  <Sparkles class="size-4" :class="promptAssistAction === item.action ? 'ai-orbit' : ''" />
                  <span>
                    <strong>{{ item.label }}</strong>
                    <small>{{ item.description }}</small>
                  </span>
                </button>
              </div>
            </div>
          </div>
      </div>

      <div v-if="promptAssistNote" class="mt-2 text-[12px] font-medium text-[#4F7CFF]">{{ promptAssistNote }}</div>
  </section>
  <Teleport to="body">
    <button v-if="openSettingCard" type="button" class="composer-settings-backdrop" aria-label="关闭设置" @click="closeSettingCards" />
  </Teleport>
  <Teleport to="body">
    <div
      v-if="referencePreview"
      data-reference-preview
      class="reference-image-preview fixed z-40 rounded-xl border border-black/[0.08] bg-white p-2 shadow-[0_18px_44px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[#171a21]"
      :style="{ left: `${referencePreview.left}px`, top: `${referencePreview.top}px`, width: `${referencePreview.width}px` }"
    >
      <img :src="referencePreview.src" :alt="referencePreview.name" class="max-h-[min(62vh,340px)] w-full rounded-lg bg-slate-100 object-contain dark:bg-white/[0.06]" />
      <div class="mt-2 truncate px-1 text-[12px] font-medium text-slate-600 dark:text-stone-300">{{ referencePreview.name }}</div>
    </div>
  </Teleport>
</template>

<style scoped>
.composer-prompt-shell {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  transition: border-color 180ms ease, box-shadow 180ms ease, background-color 180ms ease;
}

.reference-image-preview {
  pointer-events: none;
  animation: reference-preview-in 150ms var(--studio-ease);
}

.composer-prompt-shell::before {
  content: "";
  pointer-events: none;
  position: absolute;
  z-index: 1;
}

.composer-prompt-shell::before {
  --prompt-border-angle: 0deg;
  inset: 0;
  border-radius: inherit;
  padding: 1.5px;
  background: conic-gradient(
    from var(--prompt-border-angle),
    rgb(79 124 255 / 0) 0deg,
    rgb(79 124 255 / 0) 210deg,
    rgb(79 124 255 / 0.22) 236deg,
    rgb(79 124 255 / 0.9) 264deg,
    rgb(20 184 166 / 0.86) 292deg,
    rgb(245 158 11 / 0.72) 318deg,
    rgb(109 94 247 / 0.88) 340deg,
    rgb(79 124 255 / 0) 360deg
  );
  opacity: 0.34;
  transition: opacity 180ms ease;
  animation: prompt-border-orbit 3.4s linear infinite;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
}

.composer-prompt-shell.is-active::before {
  opacity: 0.62;
  padding: 2px;
}

.composer-prompt-shell.is-focused {
  border-color: rgb(79 124 255 / 0.32);
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.09), 0 16px 34px rgb(15 23 42 / 0.07);
}

.composer-prompt-shell--home {
  max-width: 920px;
  margin-inline: auto;
}

.composer-prompt-shell--home.is-focused {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.1), 0 18px 46px rgb(15 23 42 / 0.1);
}

.composer-prompt-shell--home .composer-input-panel.is-focused {
  background: transparent;
}

.composer-prompt-shell--home .composer-input-panel.is-focused textarea {
  box-shadow: none;
}

.composer-prompt-shell--home textarea {
  scrollbar-width: thin;
}

.composer-home-toolbar {
  position: relative;
  z-index: 2;
}

.composer-prompt-shell--home .composer-home-toolbar {
  margin-top: 0.65rem;
}

.composer-prompt-shell--home .composer-setting-card,
.composer-prompt-shell--home .composer-more-button {
  min-height: 2.35rem;
  border-radius: 999px;
  background: rgb(255 255 255);
}

.composer-prompt-shell--home .composer-setting-card-text strong {
  max-width: 7.5rem;
}

.composer-prompt-shell--home .composer-setting-card-panel {
  max-width: 920px;
  margin-inline: auto;
  border-radius: 1rem;
  background: rgb(255 255 255 / 0.84);
}

.dark .composer-prompt-shell--home .composer-setting-card,
.dark .composer-prompt-shell--home .composer-more-button,
.dark .composer-prompt-shell--home .composer-setting-card-panel {
  background: rgb(255 255 255 / 0.06);
}

.composer-prompt-shell.is-focused::before,
.composer-prompt-shell.is-submitting::before,
.composer-prompt-shell.is-dragging::before {
  opacity: 0.82;
}

.composer-prompt-shell.is-submitting::before {
  animation-duration: 1.35s;
}

.dark .composer-prompt-shell.is-focused {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.14), 0 16px 34px rgb(0 0 0 / 0.24);
}

.composer-input-panel {
  position: relative;
  z-index: 2;
}

.composer-input-panel.is-focused {
  border-color: rgb(79 124 255 / 0.26);
  background: rgb(79 124 255 / 0.035);
}

.composer-input-panel textarea {
  transition: border-color 160ms ease, box-shadow 160ms ease, background-color 160ms ease;
}

.composer-input-panel.is-focused textarea {
  border-color: rgb(79 124 255 / 0.36);
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.1);
}

@property --prompt-border-angle {
  syntax: "<angle>";
  inherits: false;
  initial-value: 0deg;
}

.composer-setting-card {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.5rem;
  max-width: min(100%, 12.5rem);
  padding: 0.4rem 0.65rem;
  border: 1px solid rgb(15 23 42 / 0.06);
  border-radius: 0.75rem;
  background: rgb(248 250 252);
  color: rgb(51 65 85);
  font-size: 0.75rem;
  font-weight: 600;
  text-align: left;
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.composer-setting-card:hover,
.composer-setting-card:focus-visible,
.composer-setting-card.is-selected,
.composer-more-button:hover,
.composer-more-button:focus-visible,
.composer-more-button.is-selected {
  border-color: rgb(79 124 255 / 0.32);
  background: rgb(79 124 255 / 0.08);
  color: rgb(49 91 232);
  outline: none;
}

.composer-setting-card:focus-visible,
.composer-more-button:focus-visible {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.14);
}

.composer-setting-card-text {
  display: grid;
  min-width: 0;
  line-height: 1.1;
}

.composer-setting-card-text span {
  color: rgb(100 116 139);
  font-size: 0.68rem;
  font-weight: 600;
}

.composer-setting-card-text strong {
  overflow: hidden;
  max-width: 8.5rem;
  color: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-setting-card-panel {
  position: relative;
  z-index: 2;
  overflow: hidden;
}

.composer-preferences-panel {
  display: grid;
  gap: 0.9rem;
}

.composer-preferences-heading {
  display: flex;
  min-height: 2.75rem;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid rgb(15 23 42 / 0.06);
  padding-bottom: 0.7rem;
  color: rgb(15 23 42);
  font-size: 0.875rem;
}

.composer-panel-close {
  display: inline-grid;
  width: 2.75rem;
  height: 2.75rem;
  place-items: center;
  border: 0;
  border-radius: 0.625rem;
  background: transparent;
  color: rgb(100 116 139);
}

.composer-panel-close:hover,
.composer-panel-close:focus-visible {
  background: rgb(15 23 42 / 0.05);
  color: rgb(49 91 232);
  outline: none;
}

.composer-preferences-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1.1fr) minmax(220px, 1fr);
  gap: 0.9rem;
}

.composer-preference-field {
  display: grid;
  min-width: 0;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  border: 0;
}

.composer-preference-field > span,
.composer-preference-field > legend {
  padding: 0;
  color: rgb(100 116 139);
  font-size: 0.75rem;
  font-weight: 700;
}

.composer-preference-field--canvas {
  grid-column: 1 / -1;
}

.composer-settings-backdrop {
  display: none;
}

.composer-more-button {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 2.5rem;
  padding: 0.4rem 0.75rem;
  border: 1px solid rgb(15 23 42 / 0.06);
  border-radius: 0.75rem;
  background: rgb(255 255 255);
  color: rgb(71 85 105);
  font-size: 0.8125rem;
  font-weight: 700;
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.composer-more-action {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 0.65rem;
  padding: 0.7rem 0.75rem;
  border: 1px solid rgb(15 23 42 / 0.06);
  border-radius: 0.75rem;
  background: rgb(255 255 255);
  color: rgb(51 65 85);
  text-align: left;
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease;
}

.composer-more-action:hover,
.composer-more-action:focus-visible {
  border-color: rgb(79 124 255 / 0.28);
  background: rgb(79 124 255 / 0.08);
  color: rgb(49 91 232);
  outline: none;
}

.composer-more-action:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.composer-more-action span {
  display: grid;
  min-width: 0;
  gap: 0.15rem;
}

.composer-more-action strong {
  font-size: 0.82rem;
  font-weight: 700;
  line-height: 1.15;
}

.composer-more-action small {
  overflow: hidden;
  color: rgb(100 116 139);
  font-size: 0.72rem;
  font-weight: 500;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-setting-card-panel-clip {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  overflow: hidden;
  transform: translateY(-4px);
  transition:
    grid-template-rows 280ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 180ms ease,
    transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}

.composer-setting-card-panel-clip.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  transform: translateY(0);
}

.composer-setting-card-panel-clip > .composer-setting-card-panel {
  min-height: 0;
}

.dark .composer-setting-card {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.06);
  color: rgb(231 229 228);
}

.dark .composer-setting-card:hover,
.dark .composer-setting-card:focus-visible,
.dark .composer-setting-card.is-selected,
.dark .composer-more-button:hover,
.dark .composer-more-button:focus-visible,
.dark .composer-more-button.is-selected {
  border-color: rgb(79 124 255 / 0.36);
  background: rgb(79 124 255 / 0.16);
  color: rgb(191 219 254);
}

.dark .composer-more-button,
.dark .composer-more-action {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.06);
  color: rgb(231 229 228);
}

.dark .composer-more-action:hover,
.dark .composer-more-action:focus-visible {
  border-color: rgb(79 124 255 / 0.36);
  background: rgb(79 124 255 / 0.16);
  color: rgb(191 219 254);
}

.dark .composer-more-action small {
  color: rgb(168 162 158);
}

@keyframes prompt-border-orbit {
  to {
    --prompt-border-angle: 360deg;
  }
}

@keyframes reference-preview-in {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.985);
  }

  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .composer-prompt-shell::before {
    animation: none;
  }

  .reference-image-preview {
    animation: none;
  }

  .composer-setting-card-panel-clip {
    transition: opacity 120ms ease;
  }

  .composer-setting-card-panel-clip,
  .composer-setting-card-panel-clip.is-open {
    transform: none;
  }
}

@media (hover: none) {
  .reference-image-preview {
    display: none;
  }
}

.composer-prompt-shell {
  overflow: visible;
  max-width: 920px;
  margin-inline: auto;
  border-radius: 16px !important;
  background: rgb(255 255 255) !important;
  box-shadow: 0 1px 2px rgb(15 23 42 / 0.04) !important;
}

.composer-prompt-shell::before {
  display: none;
}

.composer-prompt-shell.is-focused {
  border-color: rgb(79 124 255 / 0.24) !important;
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.08), 0 10px 26px rgb(15 23 42 / 0.07) !important;
}

.composer-control-surface {
  position: relative;
  z-index: 2;
}

.composer-reference-row {
  position: relative;
  z-index: 2;
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.composer-reference-strip {
  display: flex;
  min-width: 0;
  max-width: 100%;
  flex: none;
  gap: 0.5rem;
  overflow-x: auto;
  padding-bottom: 1px;
}

.composer-reference-assist {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 0.55rem;
  overflow-x: auto;
  padding-bottom: 1px;
}

.composer-reference-assist-button {
  display: inline-flex;
  min-width: 9.5rem;
  min-height: 3.35rem;
  align-items: center;
  gap: 0.55rem;
  padding: 0.55rem 0.75rem;
  border: 1px solid rgb(15 23 42 / 0.06);
  border-radius: 12px;
  background: rgb(255 255 255);
  color: rgb(100 116 139);
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease, background-color 160ms ease, color 160ms ease, opacity 160ms ease;
}

.composer-reference-assist-button:hover,
.composer-reference-assist-button:focus-visible {
  border-color: rgb(79 124 255 / 0.26);
  background: rgb(79 124 255 / 0.055);
  color: rgb(49 91 232);
  outline: none;
}

.composer-reference-assist-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.composer-reference-assist-button span {
  display: grid;
  min-width: 0;
  gap: 0.12rem;
}

.composer-reference-assist-button strong {
  color: rgb(71 85 105);
  font-size: 0.78rem;
  font-weight: 750;
  line-height: 1.15;
}

.composer-reference-assist-button small {
  overflow: hidden;
  color: rgb(148 163 184);
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-video-row {
  position: relative;
  z-index: 2;
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

.composer-video-chip {
  display: inline-flex;
  min-width: 0;
  max-width: min(100%, 22rem);
  align-items: center;
  gap: 0.55rem;
  border: 1px solid rgb(79 124 255 / 0.2);
  border-radius: 10px;
  background: rgb(79 124 255 / 0.055);
  padding: 0.45rem 0.5rem 0.45rem 0.6rem;
  color: rgb(51 65 85);
}

.composer-video-icon {
  display: inline-grid;
  width: 2rem;
  height: 2rem;
  flex: none;
  place-items: center;
  border-radius: 8px;
  background: rgb(79 124 255 / 0.12);
  color: rgb(49 91 232);
}

.composer-video-meta {
  display: grid;
  min-width: 0;
  gap: 0.05rem;
  line-height: 1.25;
}

.composer-video-meta strong {
  overflow: hidden;
  color: rgb(30 41 59);
  font-size: 0.76rem;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-video-meta small {
  color: rgb(100 116 139);
  font-size: 0.68rem;
  font-weight: 600;
}

.composer-video-remove {
  display: inline-grid;
  width: 2rem;
  height: 2rem;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(100 116 139);
  cursor: pointer;
  transition: background-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.composer-video-remove:hover,
.composer-video-remove:focus-visible {
  background: rgb(15 23 42 / 0.055);
  color: rgb(225 29 72);
  outline: none;
}

.composer-video-remove:focus-visible {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.12);
}

.composer-settings-bar {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.7rem 1rem 0.6rem;
  border-bottom: 1px solid rgb(15 23 42 / 0.06);
}

.composer-setting-group {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.55rem;
}

.composer-setting-group--trailing {
  flex: none;
}

.composer-inline-setting {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 0.55rem;
  min-height: 2.75rem;
  padding: 0 0.85rem;
  border: 0;
  border-right: 1px solid rgb(15 23 42 / 0.06);
  background: transparent;
  color: rgb(71 85 105);
  font-size: 0.78rem;
  font-weight: 650;
  cursor: pointer;
  transition: color 160ms ease, background-color 160ms ease, box-shadow 160ms ease;
}

.composer-inline-setting:first-child {
  padding-left: 0;
}

.composer-inline-setting--engine {
  cursor: default;
}

.composer-inline-setting:hover,
.composer-inline-setting:focus-visible,
.composer-inline-setting.is-selected {
  color: rgb(49 91 232);
  outline: none;
}

.composer-inline-setting:focus-visible {
  border-radius: 8px;
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.12);
}

.composer-inline-label {
  flex: none;
  color: rgb(100 116 139);
  font-weight: 650;
}

.composer-inline-setting strong {
  overflow: hidden;
  max-width: 8.5rem;
  color: rgb(15 23 42);
  font-size: 0.78rem;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-count-stepper {
  display: inline-flex;
  height: 2.75rem;
  align-items: center;
  overflow: hidden;
  border-radius: 8px;
  background: rgb(248 250 252);
  color: rgb(71 85 105);
}

.composer-count-stepper button {
  display: inline-grid;
  height: 2.75rem;
  min-width: 2.75rem;
  place-items: center;
  border: 0;
  background: transparent;
  color: inherit;
  font-size: 0.82rem;
  font-weight: 750;
  cursor: pointer;
}

.composer-count-stepper button:hover,
.composer-count-stepper button:focus-visible,
.composer-count-stepper.is-selected {
  color: rgb(49 91 232);
  outline: none;
}

.composer-count-value {
  gap: 0.35rem;
  min-width: 3.8rem;
  grid-auto-flow: column;
}

.composer-count-value span {
  color: rgb(100 116 139);
  font-size: 0.72rem;
  font-weight: 650;
}

.composer-count-value strong {
  color: rgb(15 23 42);
  font-size: 0.82rem;
  font-weight: 800;
}

.composer-input-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 0.75rem;
  padding: 1.05rem 1.15rem 0.85rem;
  background: rgb(255 255 255);
  transition: background-color 160ms ease;
}

.composer-input-panel.is-focused {
  background: rgb(79 124 255 / 0.018);
}

.composer-input-panel.is-focused textarea {
  box-shadow: none;
}

.composer-main-textarea {
  width: 100%;
  min-width: 0;
  resize: none;
  overflow-y: hidden;
  border: 0;
  background: transparent;
  color: rgb(15 23 42);
  font-size: 0.95rem;
  line-height: 1.65;
  outline: none;
  scrollbar-width: thin;
}

.composer-main-textarea::placeholder {
  color: rgb(148 163 184);
}

.composer-send-button {
  display: inline-grid;
  width: 2.75rem;
  height: 2.75rem;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 9px;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
  transition: background-color 160ms ease, transform 120ms ease, opacity 160ms ease;
}

.composer-send-button:hover {
  background: #1d4ed8;
}

.composer-send-button:active {
  transform: scale(0.94);
}

.composer-send-button:focus-visible {
  outline: 2px solid rgb(37 99 235 / 0.35);
  outline-offset: 2px;
}

.composer-send-button:disabled {
  cursor: not-allowed;
  background: rgb(148 163 184);
  opacity: 0.7;
  transform: none;
}

.composer-bottom-bar {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: 2.85rem;
  padding: 0.55rem 1rem;
  border-top: 1px solid rgb(15 23 42 / 0.06);
}

.composer-tool-group {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}

.composer-tool-group--trailing {
  flex: none;
}

.composer-tool-button {
  display: inline-flex;
  min-height: 2.75rem;
  align-items: center;
  gap: 0.42rem;
  padding: 0 0.7rem;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(100 116 139);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
}

.composer-tool-button:hover,
.composer-tool-button:focus-visible {
  background: rgb(15 23 42 / 0.045);
  color: rgb(49 91 232);
  outline: none;
}

.composer-tool-button:focus-visible {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.12);
}

.composer-tool-button:disabled {
  cursor: wait;
  opacity: 0.58;
}

.composer-submit-status {
  color: rgb(100 116 139);
  font-size: 0.74rem;
  font-weight: 650;
}

.composer-more-button {
  display: inline-grid;
  width: 2.75rem;
  height: 2.75rem;
  min-height: 2.75rem;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(148 163 184);
}

.composer-more-button:hover,
.composer-more-button:focus-visible,
.composer-more-button.is-selected {
  background: rgb(15 23 42 / 0.045);
  color: rgb(49 91 232);
  outline: none;
}

.composer-more-button:focus-visible {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.12);
}

.dark .composer-prompt-shell {
  border-color: rgb(255 255 255 / 0.1) !important;
  background: #171a21 !important;
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.24) !important;
}

.dark .composer-prompt-shell.is-focused {
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.14), 0 10px 26px rgb(0 0 0 / 0.25) !important;
}

.dark .composer-reference-assist-button {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.05);
  color: rgb(168 162 158);
}

.dark .composer-reference-assist-button:hover,
.dark .composer-reference-assist-button:focus-visible {
  border-color: rgb(79 124 255 / 0.34);
  background: rgb(79 124 255 / 0.13);
  color: rgb(191 219 254);
}

.dark .composer-reference-assist-button strong {
  color: rgb(231 229 228);
}

.dark .composer-reference-assist-button small {
  color: rgb(168 162 158);
}

.dark .composer-video-chip {
  border-color: rgb(79 124 255 / 0.28);
  background: rgb(79 124 255 / 0.13);
  color: rgb(214 211 209);
}

.dark .composer-video-icon {
  background: rgb(79 124 255 / 0.2);
  color: rgb(191 219 254);
}

.dark .composer-video-meta strong {
  color: rgb(245 245 244);
}

.dark .composer-video-meta small,
.dark .composer-video-remove {
  color: rgb(168 162 158);
}

.dark .composer-video-remove:hover,
.dark .composer-video-remove:focus-visible {
  background: rgb(255 255 255 / 0.08);
  color: rgb(251 113 133);
}

.dark .composer-settings-bar,
.dark .composer-bottom-bar {
  border-color: rgb(255 255 255 / 0.08);
}

.dark .composer-inline-setting {
  border-right-color: rgb(255 255 255 / 0.08);
  color: rgb(214 211 209);
}

.dark .composer-inline-label,
.dark .composer-count-value span,
.dark .composer-tool-button,
.dark .composer-submit-status {
  color: rgb(168 162 158);
}

.dark .composer-inline-setting strong,
.dark .composer-count-value strong {
  color: rgb(245 245 244);
}

.dark .composer-count-stepper {
  background: rgb(255 255 255 / 0.06);
  color: rgb(214 211 209);
}

.dark .composer-preferences-heading {
  border-color: rgb(255 255 255 / 0.08);
  color: rgb(245 245 244);
}

.dark .composer-input-panel {
  background: #171a21;
}

.dark .composer-input-panel.is-focused {
  background: rgb(79 124 255 / 0.055);
}

.dark .composer-main-textarea {
  color: rgb(250 250 249);
}

.dark .composer-main-textarea::placeholder {
  color: rgb(168 162 158);
}

.dark .composer-more-button {
  color: rgb(168 162 158);
}

.dark .composer-inline-setting:hover,
.dark .composer-inline-setting:focus-visible,
.dark .composer-inline-setting.is-selected,
.dark .composer-count-stepper button:hover,
.dark .composer-count-stepper button:focus-visible,
.dark .composer-count-stepper.is-selected,
.dark .composer-more-button:hover,
.dark .composer-more-button:focus-visible,
.dark .composer-more-button.is-selected,
.dark .composer-tool-button:hover,
.dark .composer-tool-button:focus-visible {
  background: rgb(79 124 255 / 0.12);
  color: rgb(191 219 254);
}

@media (max-width: 760px) {
  .composer-reference-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .composer-reference-strip,
  .composer-reference-assist {
    width: 100%;
  }

  .composer-reference-assist-button {
    min-width: 8.75rem;
  }

  .composer-settings-bar {
    align-items: center;
    flex-direction: row;
    gap: 0.4rem;
    overflow-x: auto;
    padding: 0.45rem 0.55rem;
    scrollbar-width: none;
  }

  .composer-setting-group,
  .composer-setting-group--trailing {
    flex: none;
    flex-wrap: nowrap;
  }

  .composer-inline-setting {
    border-right: 0;
    padding: 0 0.55rem;
  }

  .composer-inline-setting:first-child {
    padding-left: 0.55rem;
  }

  .composer-input-panel {
    padding: 0.7rem 0.75rem 0.6rem;
  }

  .composer-bottom-bar {
    align-items: center;
    flex-direction: row;
    min-height: 2.75rem;
    overflow: hidden;
    padding: 0.25rem 0.4rem;
  }

  .composer-tool-group {
    width: 100%;
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .composer-tool-group--trailing {
    display: none;
  }

  .composer-tool-button {
    flex: none;
    padding: 0 0.55rem;
  }

  .composer-settings-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9;
    display: block;
    width: 100%;
    height: 100%;
    border: 0;
    background: rgb(15 23 42 / 0.38);
  }

  .composer-setting-card-panel-clip {
    display: none;
    height: 0;
  }

  .composer-setting-card-panel-clip.is-open {
    display: block;
    height: 0;
    overflow: visible;
    opacity: 1;
    transform: none;
  }

  .composer-setting-card-panel {
    position: fixed;
    right: 0.5rem;
    bottom: max(0.5rem, env(safe-area-inset-bottom));
    left: 0.5rem;
    z-index: 30;
    max-height: min(76dvh, 640px);
    overflow-y: auto;
    margin: 0 !important;
    border: 0 !important;
    border-radius: 16px !important;
    background: rgb(255 255 255) !important;
    padding: 0.85rem !important;
    box-shadow: 0 8px 24px rgb(15 23 42 / 0.2);
  }

  .dark .composer-setting-card-panel {
    background: #171a21 !important;
  }

  .composer-preferences-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .composer-preference-field--canvas {
    grid-column: auto;
  }
}

@media (max-width: 480px) {
  .composer-settings-bar.is-agent-mode {
    align-items: stretch;
    overflow-x: visible;
    padding: 0.45rem 0.7rem;
  }

  .composer-settings-bar.is-agent-mode > .composer-setting-group:first-child {
    display: grid;
    width: 100%;
    flex: 1 1 auto;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.2rem;
  }

  .composer-settings-bar.is-agent-mode .composer-inline-setting {
    width: 100%;
    min-height: 2.5rem;
    padding: 0 0.15rem;
  }

  .composer-settings-bar.is-agent-mode .composer-inline-setting--engine {
    justify-content: space-between;
  }

  .composer-settings-bar.is-agent-mode .composer-inline-setting--preferences {
    border-top: 1px solid rgb(15 23 42 / 0.06);
  }

  .composer-settings-bar.is-agent-mode .composer-inline-setting--preferences strong {
    margin-left: auto;
  }

  .composer-settings-bar.is-agent-mode .composer-setting-group--trailing {
    display: none;
  }

  .dark .composer-settings-bar.is-agent-mode .composer-inline-setting--preferences {
    border-top-color: rgb(255 255 255 / 0.08);
  }
}
</style>
