<script setup lang="ts">
import {
  ArrowUp,
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
  X,
  Zap,
} from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { analyzeImagePrompt, type ImageModel } from "@/lib/api";
import { formatImageModel, imageModelFeatures } from "@/lib/image-models";
import type { StoredReferenceImage } from "@/stores/image-conversations";

const props = defineProps<{
  imageModels: ImageModel[];
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: StoredReferenceImage[];
  batchProductImage: StoredReferenceImage | null;
  batchFolderImages: StoredReferenceImage[];
  isSubmitting: boolean;
}>();

const emit = defineEmits<{
  submit: [];
  createDraft: [];
  referenceFiles: [files: File[]];
  removeReference: [index: number];
  pickBatchProduct: [];
  pickBatchFolder: [];
  clearBatch: [];
  openLightbox: [images: Array<{ id: string; src: string; name?: string }>, index: number];
}>();

const prompt = defineModel<string>("prompt", { required: true });
const imageCount = defineModel<string>("imageCount", { required: true });
const imageRatio = defineModel<string>("imageRatio", { required: true });
const imageTier = defineModel<string>("imageTier", { required: true });
const imageWidth = defineModel<string>("imageWidth", { required: true });
const imageHeight = defineModel<string>("imageHeight", { required: true });
const imageQuality = defineModel<string>("imageQuality", { required: true });
const imageModel = defineModel<ImageModel>("imageModel", { required: true });
const preserveSubject = defineModel<boolean>("preserveSubject", { required: true });

const fileInput = ref<HTMLInputElement | null>(null);
const textarea = ref<HTMLTextAreaElement | null>(null);
const settingCards = ref<HTMLElement | null>(null);
const isDragging = ref(false);
const isFocused = ref(false);
type SettingCard = "model" | "canvas" | "count" | "more";
type PromptAssistAction = "suggest" | "optimize" | "enhance";
const openSettingCard = ref<SettingCard | null>(null);
const promptAssistAction = ref<PromptAssistAction | null>(null);
const promptAssistNote = ref("");

const assistOptions: { action: PromptAssistAction; label: string; description: string }[] = [
  { action: "suggest", label: "建议", description: "根据参考图生成方向" },
  { action: "optimize", label: "优化", description: "整理现有提示词" },
  { action: "enhance", label: "润色", description: "强化细节和质感" },
];
const qualityOptions = [
  { value: "auto", label: "自动" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
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
const countOptions = ["1", "2", "3", "4", "6", "8"];
const previewWidth = 280;
const previewMaxHeight = 340;
const promptMinHeight = 108;
const promptMaxHeight = 240;

const modelLabel = computed(() => formatImageModel(imageModel.value));
const modelFeatures = computed(() => imageModelFeatures(imageModel.value));
const qualityLabel = computed(() => qualityOptions.find((item) => item.value === imageQuality.value)?.label || "自动");
const canvasLabel = computed(() => imageRatio.value === "auto" ? "自动" : imageRatio.value);
const countLabel = computed(() => `${imageCount.value || 1} 张`);
const hasReferences = computed(() => props.referenceImages.length > 0);
const hasBatch = computed(() => Boolean(props.batchProductImage || props.batchFolderImages.length));
const isBatchReplaceMode = computed(() => Boolean(props.batchProductImage && props.batchFolderImages.length));
const isFolderBatchMode = computed(() => Boolean(!props.batchProductImage && props.batchFolderImages.length));
const canSubmit = computed(() => !props.isSubmitting && (Boolean(prompt.value.trim()) || isBatchReplaceMode.value));
const canPreserve = computed(() => hasReferences.value || hasBatch.value);
const promptPlaceholder = computed(() => isBatchReplaceMode.value ? "补充批量替换要求..." : isFolderBatchMode.value ? "输入要套用到每张文件夹图片的生成要求..." : hasReferences.value ? "描述你希望如何修改参考图..." : "输入商品图片生成需求...");
const submitText = computed(() => props.isSubmitting ? "提交中" : isBatchReplaceMode.value ? "批量替换" : isFolderBatchMode.value ? "批量生图" : hasReferences.value ? "编辑图片" : "生成图片");
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
function clampCount(value: string) {
  imageCount.value = value === "" ? "" : String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}
function resizePromptTextarea() {
  const element = textarea.value;
  if (!element) return;
  element.style.height = "auto";
  const nextHeight = Math.min(Math.max(element.scrollHeight, promptMinHeight), promptMaxHeight);
  element.style.height = `${nextHeight}px`;
  element.style.overflowY = element.scrollHeight > promptMaxHeight ? "auto" : "hidden";
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
  openSettingCard.value = openSettingCard.value === card ? null : card;
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
function pickReferences() {
  fileInput.value?.click();
}
function onFiles(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []).filter((file) => file.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|svg)$/i.test(file.name));
  if (files.length) emit("referenceFiles", files);
  input.value = "";
}
function onPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) return;
  event.preventDefault();
  emit("referenceFiles", files);
}
function onDrop(event: DragEvent) {
  event.preventDefault();
  isDragging.value = false;
  const files = Array.from(event.dataTransfer?.files || []).filter((file) => file.type.startsWith("image/") || /\.(jpe?g|png|webp|gif|bmp|svg)$/i.test(file.name));
  if (files.length) emit("referenceFiles", files);
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
      .slice(0, 4)
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
</script>

<template>
  <section class="composer-prompt-shell rounded-[22px] border border-black/[0.08] bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#171a21]" :class="[promptShellState, isDragging ? 'is-dragging ring-4 ring-[#4F7CFF]/15' : '']" @dragenter.prevent="isDragging = true" @dragover.prevent="isDragging = true" @dragleave.self="isDragging = false" @drop="onDrop">
      <div v-if="referenceImages.length" class="mb-3 flex gap-2 overflow-x-auto">
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

      <div v-if="hasBatch" class="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.06] px-3 py-2">
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

      <div class="composer-input-panel rounded-2xl border border-black/[0.08] bg-[#F8FAFC] px-3 py-3 transition dark:border-white/10 dark:bg-white/[0.04]" :class="{ 'is-focused': isFocused }">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <label for="image-prompt-input" class="inline-flex items-center gap-2 text-[14px] font-semibold text-slate-900 dark:text-stone-50">
            <Sparkles class="size-4 text-[#315be8]" />
            输入要求
          </label>
          <span class="text-[12px] font-medium text-slate-500 dark:text-stone-400">Prompt</span>
        </div>
        <textarea
          id="image-prompt-input"
          ref="textarea"
          v-model="prompt"
          :placeholder="promptPlaceholder"
          class="w-full resize-none overflow-y-hidden rounded-xl border border-black/[0.06] bg-white px-3 py-3 text-[16px] leading-7 text-slate-950 outline-none placeholder:text-slate-500 dark:border-white/10 dark:bg-[#111317] dark:text-stone-50 dark:placeholder:text-stone-400"
          :style="{ minHeight: `${promptMinHeight}px`, maxHeight: `${promptMaxHeight}px` }"
          data-testid="image-prompt-input"
          @paste="onPaste"
          @input="resizePromptTextarea"
          @focus="isFocused = true"
          @blur="isFocused = false"
        />
      </div>
      <input ref="fileInput" type="file" accept="image/*" multiple class="hidden" data-testid="reference-file-input" @change="onFiles" />

      <div class="mt-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div ref="settingCards" class="min-w-0 flex-1" @keydown.escape="closeSettingCards">
          <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="emit('createDraft')">
            <MessageSquarePlus class="size-4" />
            新建任务
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" data-testid="pick-reference-button" @click="pickReferences">
            <ImagePlus class="size-4" />
            图片
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" data-testid="pick-batch-folder-button" @click="emit('pickBatchFolder')">
            <FolderUp class="size-4" />
            批量文件夹
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" data-testid="pick-batch-product-button" @click="emit('pickBatchProduct')">
            <PackageCheck class="size-4" />
            换商品主图
          </button>
            <button
              type="button"
              class="composer-setting-card"
              :class="{ 'is-selected': openSettingCard === 'model' }"
              :aria-expanded="openSettingCard === 'model'"
              aria-controls="composer-model-card-panel"
              data-testid="image-model-card-toggle"
              @click="toggleSettingCard('model')"
            >
              <SlidersHorizontal class="size-4" />
              <span class="composer-setting-card-text">
                <span>模型</span>
                <strong>{{ modelLabel }}</strong>
              </span>
            </button>
            <button
              type="button"
              class="composer-setting-card"
              :class="{ 'is-selected': openSettingCard === 'canvas' }"
              :aria-expanded="openSettingCard === 'canvas'"
              aria-controls="composer-canvas-card-panel"
              data-testid="image-canvas-card-toggle"
              @click="toggleSettingCard('canvas')"
            >
              <Square class="size-4" />
              <span class="composer-setting-card-text">
                <span>画布</span>
                <strong>{{ canvasLabel }}</strong>
              </span>
            </button>
            <button
              type="button"
              class="composer-setting-card"
              :class="{ 'is-selected': openSettingCard === 'count' }"
              :aria-expanded="openSettingCard === 'count'"
              aria-controls="composer-count-card-panel"
              data-testid="image-count-card-toggle"
              @click="toggleSettingCard('count')"
            >
              <Zap class="size-4" />
              <span class="composer-setting-card-text">
                <span>数量</span>
                <strong>{{ countLabel }}</strong>
              </span>
            </button>
            <button
              type="button"
              class="composer-more-button"
              :class="{ 'is-selected': openSettingCard === 'more' }"
              :aria-expanded="openSettingCard === 'more'"
              aria-controls="composer-more-card-panel"
              data-testid="image-more-card-toggle"
              @click="toggleSettingCard('more')"
            >
              <MoreHorizontal class="size-4" />
              更多
            </button>
          </div>

          <Transition name="composer-setting-card-panel">
            <div v-if="openSettingCard" class="composer-setting-card-panel mt-2 rounded-xl border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]">
              <div v-if="openSettingCard === 'model'" id="composer-model-card-panel" class="grid gap-3 sm:grid-cols-[minmax(220px,1fr)_auto]">
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
                  <label class="mb-2 block text-xs font-semibold text-slate-500">质量</label>
                  <div class="grid grid-cols-4 gap-2">
                    <button v-for="option in qualityOptions" :key="option.value" type="button" class="studio-button h-9 rounded-xl border text-[13px] font-medium" :class="option.value === imageQuality ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" @click="imageQuality = option.value">{{ option.label }}</button>
                  </div>
                  <label class="mt-2 inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" :class="canPreserve ? 'cursor-pointer' : 'cursor-not-allowed opacity-55'">
                    <input v-model="preserveSubject" type="checkbox" class="size-4 accent-[#4F7CFF]" :disabled="!canPreserve" />
                    <ShieldCheck class="size-4" />
                    主体保真
                  </label>
                </div>
              </div>

              <div v-else-if="openSettingCard === 'canvas'" id="composer-canvas-card-panel" class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
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

              <div v-else-if="openSettingCard === 'count'" id="composer-count-card-panel" class="max-w-[360px]">
                <label class="mb-2 block text-xs font-semibold text-slate-500">生成数量</label>
                <div class="grid grid-cols-3 gap-2 sm:grid-cols-6">
                  <button v-for="value in countOptions" :key="value" type="button" class="studio-button h-9 rounded-xl border text-[13px] font-medium" :class="imageCount === value ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300'" @click="imageCount = value">{{ value }} 张</button>
                </div>
                <div class="mt-2 flex items-center gap-2">
                  <input :value="imageCount" type="number" min="1" max="100" aria-label="自定义张数" class="studio-input h-10 max-w-[140px] px-3 text-center" @input="clampCount(($event.target as HTMLInputElement).value)" />
                  <span class="text-xs text-slate-400">张</span>
                </div>
              </div>

              <div v-else-if="openSettingCard === 'more'" id="composer-more-card-panel" class="grid gap-2 sm:grid-cols-3">
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
          </Transition>
        </div>
        <button type="button" class="studio-button inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-[15px] font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none dark:bg-white dark:text-slate-950 dark:hover:bg-stone-100 dark:disabled:bg-stone-700 dark:disabled:text-stone-400" :disabled="!canSubmit" data-testid="generate-submit-button" @click="emit('submit')">
          <LoaderCircle v-if="isSubmitting" class="size-4 animate-spin" />
          <ArrowUp v-else class="size-4" />
          {{ submitText }}
        </button>
      </div>

      <div v-if="promptAssistNote" class="mt-2 text-[12px] font-medium text-[#4F7CFF]">{{ promptAssistNote }}</div>
  </section>
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
  transition: opacity 180ms ease, padding 180ms ease;
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

.composer-setting-card-panel-enter-active,
.composer-setting-card-panel-leave-active {
  max-height: 320px;
  opacity: 1;
  transform: translateY(0);
  transition:
    max-height 260ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 180ms ease,
    transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}

.composer-setting-card-panel-enter-from,
.composer-setting-card-panel-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-6px);
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

  .composer-setting-card-panel-enter-active,
  .composer-setting-card-panel-leave-active {
    transition: opacity 120ms ease;
  }

  .composer-setting-card-panel-enter-from,
  .composer-setting-card-panel-leave-to {
    transform: none;
  }
}

@media (hover: none) {
  .reference-image-preview {
    display: none;
  }
}
</style>
