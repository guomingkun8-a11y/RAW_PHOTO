<script setup lang="ts">
import {
  ArrowUp,
  FolderUp,
  ImagePlus,
  LoaderCircle,
  MessageSquarePlus,
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
import { computed, ref } from "vue";

import { analyzeImagePrompt, type ImageModel, type PromptTemplate } from "@/lib/api";
import { formatImageModel, imageModelFeatures, isImageModel } from "@/lib/image-models";
import type { StoredReferenceImage } from "@/stores/image-conversations";

const props = defineProps<{
  imageModels: ImageModel[];
  promptTemplates: PromptTemplate[];
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
}>();

const prompt = defineModel<string>("prompt", { required: true });
const imageCount = defineModel<string>("imageCount", { required: true });
const imageRatio = defineModel<string>("imageRatio", { required: true });
const imageTier = defineModel<string>("imageTier", { required: true });
const imageWidth = defineModel<string>("imageWidth", { required: true });
const imageHeight = defineModel<string>("imageHeight", { required: true });
const imageQuality = defineModel<string>("imageQuality", { required: true });
const imageModel = defineModel<ImageModel>("imageModel", { required: true });
const selectedTemplateId = defineModel<number | null>("selectedTemplateId", { required: true });
const preserveSubject = defineModel<boolean>("preserveSubject", { required: true });

const fileInput = ref<HTMLInputElement | null>(null);
const textarea = ref<HTMLTextAreaElement | null>(null);
const isDragging = ref(false);
const isFocused = ref(false);
const settingsOpen = ref(false);
const promptAssistAction = ref<"suggest" | "optimize" | "enhance" | null>(null);
const promptAssistNote = ref("");

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

const modelLabel = computed(() => formatImageModel(imageModel.value));
const modelFeatures = computed(() => imageModelFeatures(imageModel.value));
const qualityLabel = computed(() => qualityOptions.find((item) => item.value === imageQuality.value)?.label || "自动");
const hasReferences = computed(() => props.referenceImages.length > 0);
const hasBatch = computed(() => Boolean(props.batchProductImage || props.batchFolderImages.length));
const batchReady = computed(() => Boolean(props.batchProductImage && props.batchFolderImages.length));
const canSubmit = computed(() => !props.isSubmitting && (Boolean(prompt.value.trim()) || batchReady.value));
const canPreserve = computed(() => hasReferences.value || hasBatch.value);
const promptPlaceholder = computed(() => batchReady.value ? "补充批量替换要求..." : hasReferences.value ? "描述你希望如何修改参考图..." : "输入商品图片生成需求...");
const submitText = computed(() => props.isSubmitting ? "提交中" : batchReady.value ? "批量替换" : hasReferences.value ? "编辑图片" : "生成图片");
const promptShellState = computed(() => ({
  "is-active": isFocused.value || Boolean(prompt.value.trim()) || hasReferences.value || hasBatch.value,
  "is-focused": isFocused.value,
  "is-submitting": props.isSubmitting,
}));

function formatModel(value: string) {
  return formatImageModel(value);
}
function clampCount(value: string) {
  imageCount.value = value === "" ? "" : String(Math.min(100, Math.max(1, Math.floor(Number(value) || 1))));
}
function setAspect(option: typeof aspectOptions[number]) {
  imageRatio.value = option.ratio;
  imageTier.value = option.tier;
  imageWidth.value = option.width;
  imageHeight.value = option.height;
}
function selectTemplate(templateId: string) {
  const value = templateId === "none" ? null : Number(templateId);
  selectedTemplateId.value = value;
  const template = props.promptTemplates.find((item) => item.id === value);
  if (!template) return;
  prompt.value = template.content;
  if (template.model && props.imageModels.includes(template.model) && isImageModel(template.model)) imageModel.value = template.model;
  if (template.quality) imageQuality.value = template.quality;
  if (template.size) {
    const matched = template.size.match(/^(\d+)x(\d+)$/);
    if (matched) {
      imageWidth.value = matched[1];
      imageHeight.value = matched[2];
      imageRatio.value = "auto";
      imageTier.value = "auto";
    }
  }
  preserveSubject.value = template.preserve_subject;
  textarea.value?.focus();
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
async function assist(action: "suggest" | "optimize" | "enhance") {
  if (!hasReferences.value || promptAssistAction.value) {
    promptAssistNote.value = "请先上传商品参考图，再让 AI 分析图片并生成 Prompt。";
    return;
  }
  promptAssistAction.value = action;
  promptAssistNote.value = "AI 正在分析参考图...";
  try {
    const result = await analyzeImagePrompt({
      action,
      mode: "single",
      prompt: prompt.value.trim(),
      images: props.referenceImages.slice(0, 4).map((image) => ({ name: image.name, dataUrl: image.dataUrl })),
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
</script>

<template>
  <section class="composer-prompt-shell rounded-[22px] border border-black/[0.08] bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#171a21]" :class="[promptShellState, isDragging ? 'is-dragging ring-4 ring-[#4F7CFF]/15' : '']" @dragenter.prevent="isDragging = true" @dragover.prevent="isDragging = true" @dragleave.self="isDragging = false" @drop="onDrop">
      <div v-if="referenceImages.length" class="mb-3 flex gap-2 overflow-x-auto">
        <div v-for="(image, index) in referenceImages" :key="`${image.name}-${index}`" class="group relative size-16 shrink-0 overflow-hidden rounded-xl border border-black/[0.06] dark:border-white/10">
          <img :src="image.dataUrl" alt="参考图" class="h-full w-full object-cover" />
          <button type="button" class="absolute right-1 top-1 inline-flex size-6 items-center justify-center rounded-lg bg-slate-950/75 text-white opacity-100 sm:opacity-0 sm:group-hover:opacity-100" aria-label="移除参考图" @click="emit('removeReference', index)">
            <X class="size-3" />
          </button>
        </div>
      </div>

      <div v-if="hasBatch" class="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.06] px-3 py-2">
        <Replace class="size-4 text-[#315be8]" />
        <div class="min-w-0 flex-1 text-xs text-slate-600 dark:text-stone-300">
          主图 {{ batchProductImage ? '已上传' : '未上传' }}，文件夹 {{ batchFolderImages.length }} 张
        </div>
        <button type="button" class="rounded-xl px-2 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50" @click="emit('clearBatch')">清空</button>
      </div>

      <textarea
        ref="textarea"
        v-model="prompt"
        :placeholder="promptPlaceholder"
        class="min-h-[96px] w-full resize-none bg-transparent px-1 py-1 text-[16px] leading-7 text-slate-950 outline-none placeholder:text-slate-400 dark:text-stone-50"
        @paste="onPaste"
        @focus="isFocused = true"
        @blur="isFocused = false"
      />
      <input ref="fileInput" type="file" accept="image/*" multiple class="hidden" @change="onFiles" />

      <section class="composer-settings mt-2 rounded-xl border border-black/[0.06] bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04]" :class="{ 'is-open': settingsOpen }">
        <button
          type="button"
          class="composer-settings-trigger flex w-full cursor-pointer flex-wrap items-center justify-between gap-2 px-3 py-2 text-left text-sm font-semibold text-slate-700 dark:text-stone-200"
          :aria-expanded="settingsOpen"
          aria-controls="composer-settings-panel"
          @click="settingsOpen = !settingsOpen"
        >
          <span class="inline-flex items-center gap-2"><SlidersHorizontal class="size-4" />模型与画布</span>
          <span class="flex flex-wrap gap-1.5 text-[11px] font-medium text-slate-500">
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ modelLabel }}</span>
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ imageWidth }} x {{ imageHeight }}</span>
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ qualityLabel }} / {{ imageCount || 1 }} 张</span>
          </span>
        </button>
        <Transition name="composer-settings-panel">
          <div v-show="settingsOpen" id="composer-settings-panel" class="composer-settings-panel overflow-hidden border-t border-black/[0.06] dark:border-white/10">
            <div class="grid gap-4 p-3 xl:grid-cols-[260px_minmax(0,1fr)_220px]">
              <div>
                <label class="mb-2 block text-xs font-semibold text-slate-500">模型</label>
                <select v-model="imageModel" class="studio-input h-10 px-3 text-sm">
                  <option v-for="model in imageModels" :key="model" :value="model">{{ formatModel(model) }} - {{ model }}</option>
                </select>
                <div class="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500 dark:text-stone-400">
                  <span class="max-w-full truncate rounded-full bg-slate-100 px-2 py-1 font-mono dark:bg-white/[0.08]">{{ imageModel }}</span>
                  <span v-for="feature in modelFeatures" :key="feature" class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 font-medium text-[#315be8]">{{ feature }}</span>
                </div>
                <div class="mt-2 grid grid-cols-4 gap-2">
                  <button v-for="option in qualityOptions" :key="option.value" type="button" class="studio-button h-9 rounded-xl border text-[13px] font-medium" :class="option.value === imageQuality ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:text-stone-300'" @click="imageQuality = option.value">{{ option.label }}</button>
                </div>
                <label class="mt-3 inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] px-3 text-[13px] font-medium text-slate-700 dark:border-white/10 dark:text-stone-200" :class="canPreserve ? 'cursor-pointer' : 'cursor-not-allowed opacity-55'">
                  <input v-model="preserveSubject" type="checkbox" class="size-4 accent-[#4F7CFF]" :disabled="!canPreserve" />
                  <ShieldCheck class="size-4" />
                  主体保真
                </label>
              </div>

              <div>
                <label class="mb-2 block text-xs font-semibold text-slate-500">画布尺寸</label>
                <div class="grid grid-cols-3 gap-2 sm:grid-cols-5">
                  <button v-for="option in aspectOptions" :key="`${option.ratio}-${option.tier}-${option.label}`" type="button" class="studio-button flex h-[58px] flex-col items-center justify-center gap-1 rounded-xl border text-[13px] font-medium" :class="option.ratio === imageRatio && option.tier === imageTier && option.width === imageWidth && option.height === imageHeight ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:text-stone-300'" @click="setAspect(option)">
                    <component :is="option.icon" class="size-4" />
                    <span>{{ option.label }}</span>
                  </button>
                </div>
                <div class="mt-2 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                  <input v-model="imageWidth" type="number" min="1" class="studio-input h-10 px-3 text-center" />
                  <span class="text-sm text-slate-400">x</span>
                  <input v-model="imageHeight" type="number" min="1" class="studio-input h-10 px-3 text-center" />
                </div>
              </div>

              <div>
                <label class="mb-2 block text-xs font-semibold text-slate-500">数量与模板</label>
                <div class="grid grid-cols-3 gap-2">
                  <button v-for="value in countOptions" :key="value" type="button" class="studio-button h-9 rounded-xl border text-[13px] font-medium" :class="imageCount === value ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:text-stone-300'" @click="imageCount = value">{{ value }} 张</button>
                </div>
                <div class="mt-2 flex items-center gap-2">
                  <input :value="imageCount" type="number" min="1" max="100" aria-label="自定义张数" class="studio-input h-10 px-3 text-center" @input="clampCount(($event.target as HTMLInputElement).value)" />
                  <span class="text-xs text-slate-400">张</span>
                </div>
                <select :value="selectedTemplateId == null ? 'none' : String(selectedTemplateId)" class="studio-input mt-2 h-10 px-3 text-sm" @change="selectTemplate(($event.target as HTMLSelectElement).value)">
                  <option value="none">不使用模板</option>
                  <option v-for="item in promptTemplates" :key="item.id" :value="String(item.id)">{{ item.name }}</option>
                </select>
              </div>
            </div>
          </div>
        </Transition>
      </section>

      <div class="mt-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="emit('createDraft')">
            <MessageSquarePlus class="size-4" />
            新建任务
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="pickReferences">
            <ImagePlus class="size-4" />
            图片
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="emit('pickBatchFolder')">
            <FolderUp class="size-4" />
            文件夹
          </button>
          <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="emit('pickBatchProduct')">
            <PackageCheck class="size-4" />
            主图
          </button>
          <button v-for="item in [{ action: 'suggest', label: '建议' }, { action: 'optimize', label: '优化' }, { action: 'enhance', label: '润色' }]" :key="item.action" type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" :disabled="!hasReferences || Boolean(promptAssistAction)" @click="assist(item.action as 'suggest' | 'optimize' | 'enhance')">
            <Sparkles class="size-4" :class="promptAssistAction === item.action ? 'ai-orbit' : ''" />
            {{ item.label }}
          </button>
        </div>
        <button type="button" class="studio-button inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-[15px] font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none dark:bg-white dark:text-slate-950 dark:hover:bg-stone-100 dark:disabled:bg-stone-700 dark:disabled:text-stone-400" :disabled="!canSubmit" @click="emit('submit')">
          <LoaderCircle v-if="isSubmitting" class="size-4 animate-spin" />
          <ArrowUp v-else class="size-4" />
          {{ submitText }}
        </button>
      </div>

      <div v-if="promptAssistNote" class="mt-2 text-[12px] font-medium text-[#4F7CFF]">{{ promptAssistNote }}</div>
  </section>
</template>

<style scoped>
.composer-prompt-shell {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  transition: border-color 180ms ease, box-shadow 180ms ease, background-color 180ms ease;
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

.composer-settings {
  overflow: hidden;
  transition: border-color 220ms ease, background-color 220ms ease, box-shadow 220ms ease;
}

.composer-settings.is-open {
  border-color: rgb(79 124 255 / 0.18);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.68);
}

.composer-settings-trigger {
  transition: background-color 180ms ease, color 180ms ease;
}

.composer-settings-trigger:hover,
.composer-settings-trigger:focus-visible {
  background: rgb(79 124 255 / 0.055);
  color: rgb(49 91 232);
  outline: none;
}

.composer-settings-panel-enter-active,
.composer-settings-panel-leave-active {
  max-height: 720px;
  opacity: 1;
  transform: translateY(0);
  transition:
    max-height 520ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 360ms ease,
    transform 520ms cubic-bezier(0.16, 1, 0.3, 1);
}

.composer-settings-panel-enter-from,
.composer-settings-panel-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-10px);
}

@property --prompt-border-angle {
  syntax: "<angle>";
  inherits: false;
  initial-value: 0deg;
}

@keyframes prompt-border-orbit {
  to {
    --prompt-border-angle: 360deg;
  }
}

@media (prefers-reduced-motion: reduce) {
  .composer-prompt-shell::before {
    animation: none;
  }

  .composer-settings-panel-enter-active,
  .composer-settings-panel-leave-active {
    transition: opacity 120ms ease;
  }

  .composer-settings-panel-enter-from,
  .composer-settings-panel-leave-to {
    transform: none;
  }
}
</style>
