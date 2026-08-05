<script setup lang="ts">
import { Archive, Check, Clock3, Copy, Download, Edit3, LoaderCircle, RefreshCw, Trash2, XCircle } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { toast } from "vue-sonner";

import { downloadImageTaskZip } from "@/lib/api";
import { formatImageModel } from "@/lib/image-models";
import type { ImageConversation, ImageTurn, StoredImage, StoredReferenceImage } from "@/stores/image-conversations";

const props = withDefaults(defineProps<{
  conversation: ImageConversation | null;
  allowTimeoutRetryContinue?: boolean;
  timeoutRetry?: { taskId: string; taskError: string } | null;
  userName?: string;
  userInitial?: string;
  userAvatarUrl?: string;
  formatConversationTime: (value: string) => string;
}>(), { conversation: null, allowTimeoutRetryContinue: false, timeoutRetry: null, userName: "用户", userInitial: "U", userAvatarUrl: "" });

const emit = defineEmits<{
  openLightbox: [images: Array<{ id: string; src: string; name?: string }>, index: number];
  continueEdit: [image: StoredImage | StoredReferenceImage];
  applyPromptSuggestion: [prompt: string];
  focusPromptInput: [];
  deletePrompt: [turnId: string];
  deleteResults: [turnId: string];
  reuseTurnConfig: [turnId: string];
  regenerateTurn: [turnId: string];
  retryImage: [turnId: string, imageId: string];
  cancelTurn: [turnId: string];
  timeoutRetryContinue: [];
  timeoutRetryCancel: [];
  dismissErrors: [turnId: string];
}>();

const now = ref(Date.now());
const downloadingTurnId = ref<string | null>(null);
const expandedTurnIds = ref<Set<string>>(new Set());
const promptSuggestions = [
  {
    label: "电商白底主图",
    prompt: "生成一张 1:1 电商平台白底商品主图。商品主体放在画面中央，占画面 75%-85%，边缘轮廓清晰完整，不裁切、不变形。背景为纯净白色或极浅灰白色，无杂物、无阴影脏边。保留商品包装结构、Logo、颜色、材质和可见文字，不新增夸张促销文案、徽章、排行榜、认证标识或百分比承诺。整体光线柔和均匀，质感真实，适合淘宝、京东、拼多多等平台主图使用。",
  },
  {
    label: "真实家居场景",
    prompt: "生成一张真实家居使用场景商品图。画面为自然生活空间，干净、温暖、有真实居家氛围；使用柔和自然光，避免强烈滤镜和过度磨皮。商品放在合理使用位置，主体清晰，和环境比例协调，不要悬浮、变形或遮挡关键结构。背景可以有桌面、柜体、绿植、布艺或日常道具，但不能喧宾夺主。整体风格高级、真实、可信，适合电商详情页和社媒种草展示。",
  },
  {
    label: "商品细节特写",
    prompt: "生成一张商品细节特写图，重点突出商品材质、纹理、边缘工艺、包装质感和关键结构。使用近景构图，主体局部清晰锐利，背景干净虚化但不要过度失真。光线要能体现表面反光、凹凸、透明度或织物纹理等真实细节。不要新增无关文字、夸张功效承诺、认证徽章或百分比标识。画面要像专业商业摄影棚拍摄，可用于详情页卖点展示。",
  },
  {
    label: "竖版商品海报",
    prompt: "生成一张 9:16 竖版商品视觉海报。商品主体位于画面中下部或视觉中心，顶部预留干净标题空间，背景有层次但不要杂乱。画面需要适合手机端首屏展示，构图稳定，主体突出，光线高级，色彩有品牌感。可以加入少量生活场景或质感背景来烘托商品，但不要生成九宫格、拼图、分屏或多面板。不要添加夸张促销文字、虚假榜单、认证章或百分比承诺。",
  },
  {
    label: "四张不同场景",
    prompt: "生成 4 张不同场景的商品图。每张都必须是独立完整的一张成品图，不要拼图、不要分屏、不要九宫格、不要把 4 个场景放在同一张画布里。四张图要保持同一商品主体一致，风格统一但场景不同，可以分别表现白底主图、生活场景、细节特写、氛围海报。每张都要主体清晰、构图完整、光线自然、质感高级，不新增夸张功效文字、认证标识或无关元素。",
  },
  {
    label: "参考图主体保真",
    prompt: "根据我上传的商品参考图生成一张新的商品场景图。必须严格保持参考图中商品的外观、轮廓、颜色、Logo、包装文字、材质、比例和关键结构一致，不要改品牌、不换包装、不改变瓶身/盒型/配件关系。只允许优化背景、光线、构图和场景氛围。商品主体要清晰真实，与环境透视和阴影一致。不要生成拼图、九宫格、分屏，也不要新增夸张功效文案、认证徽章或百分比承诺。",
  },
];
const displayUserName = computed(() => props.userName.trim() || "用户");
const displayUserInitial = computed(() => {
  const source = props.userInitial.trim() || displayUserName.value;
  return source.slice(0, 1).toUpperCase() || "U";
});
const FULL_TURN_WINDOW = 6;
const COLLAPSED_PREVIEW_LIMIT = 4;
const hasLoadingImages = computed(() => Boolean(props.conversation?.turns.some((turn) => turn.images.some((image) => image.status === "loading"))));
const collapsedTurnIds = computed(() => {
  const turns = props.conversation?.turns || [];
  if (turns.length <= FULL_TURN_WINDOW) return new Set<string>();
  const keepFrom = Math.max(0, turns.length - FULL_TURN_WINDOW);
  return new Set(
    turns.flatMap((turn, index) => {
      const hasTimeoutAction = Boolean(props.timeoutRetry && turn.images.some((image) => image.taskId === props.timeoutRetry?.taskId));
      const isActive = turn.status === "queued" || turn.status === "generating" || hasTimeoutAction;
      return !isActive && index < keepFrom ? [turn.id] : [];
    }),
  );
});
let timer = 0;
function startElapsedTimer() {
  if (timer) return;
  timer = window.setInterval(() => { now.value = Date.now(); }, 1000);
}
function stopElapsedTimer() {
  if (!timer) return;
  window.clearInterval(timer);
  timer = 0;
}
watch(hasLoadingImages, (active) => {
  if (active) startElapsedTimer();
  else stopElapsedTimer();
}, { immediate: true });
watch(() => props.conversation?.id, () => { expandedTurnIds.value = new Set(); });
onBeforeUnmount(stopElapsedTimer);

function imageSrc(image: StoredImage) {
  return image.b64_json ? `data:image/png;base64,${image.b64_json}` : image.url || "";
}
function referenceSrc(image: StoredReferenceImage) {
  return image.dataUrl || image.url || "";
}
function imageItems(turn: ImageTurn) {
  return turn.images.filter((image) => image.status === "success" && imageSrc(image)).map((image) => ({ id: image.id, src: imageSrc(image), name: image.sourceName || `${image.id}.png` }));
}
function referenceItems(turn: ImageTurn) {
  return turn.referenceImages
    .map((image, index) => ({ id: `reference-${turn.id}-${index}`, src: referenceSrc(image), name: image.name || `reference-${index + 1}.png` }))
    .filter((item) => item.src);
}
function openReferenceLightbox(turn: ImageTurn, index: number) {
  const items = referenceItems(turn);
  if (!items.length) return;
  emit("openLightbox", items, Math.max(0, Math.min(index, items.length - 1)));
}
function successImages(turn: ImageTurn) {
  return turn.images.filter((image) => image.status === "success" && imageSrc(image));
}
function previewImages(turn: ImageTurn) {
  return successImages(turn).slice(-COLLAPSED_PREVIEW_LIMIT);
}
function isTurnCollapsed(turn: ImageTurn) {
  return collapsedTurnIds.value.has(turn.id) && !expandedTurnIds.value.has(turn.id);
}
function toggleTurnExpanded(turnId: string) {
  const next = new Set(expandedTurnIds.value);
  if (next.has(turnId)) next.delete(turnId);
  else next.add(turnId);
  expandedTurnIds.value = next;
}
function elapsed(image: StoredImage) {
  if (typeof image.elapsedSecs === "number") return `${image.elapsedSecs.toFixed(1)}s`;
  if (image.startTime) return `${Math.max(0, (now.value - image.startTime) / 1000).toFixed(1)}s`;
  return "等待中";
}
function statusLabel(turn: ImageTurn) {
  if (turn.status === "queued") return "排队中";
  if (turn.status === "generating") return "生成中";
  if (turn.status === "success") return "已完成";
  if (turn.status === "canceled") return "已中止";
  return "有失败";
}
function statusClass(turn: ImageTurn) {
  if (turn.status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300";
  if (turn.status === "error") return "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
  if (turn.status === "canceled") return "bg-slate-100 text-slate-600 dark:bg-white/[0.08] dark:text-stone-300";
  return "bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]";
}
function modelLabel(model: string) {
  return formatImageModel(model);
}
function downloadImage(image: StoredImage) {
  const src = imageSrc(image);
  if (!src) return;
  const link = document.createElement("a");
  link.href = src;
  link.download = image.sourceName || `${image.id}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
async function downloadZip(turn: ImageTurn) {
  if (downloadingTurnId.value) return;
  const images = turn.images.filter(
    (image) => image.status === "success" && image.taskId,
  );
  if (!images.length) {
    toast.error("当前没有可下载的图片");
    return;
  }

  const folderName = `${props.conversation?.title || "image-task"}-${turn.id}`;
  downloadingTurnId.value = turn.id;
  try {
    const blob = await downloadImageTaskZip({
      folderName,
      items: images.map((image, index) => ({
        taskId: image.taskId!,
        filename: `${String(index + 1).padStart(2, "0")}-${image.sourceName || "image.png"}`,
      })),
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${folderName}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.success(`已打包 ${images.length} 张图片`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "打包下载失败");
  } finally {
    downloadingTurnId.value = null;
  }
}
function copyPrompt(prompt: string) {
  void navigator.clipboard?.writeText(prompt);
}
</script>

<template>
  <div v-if="!conversation" class="flex min-h-[430px] items-center justify-center px-6 text-center">
    <div>
      <button
        type="button"
        class="group mx-auto block rounded-2xl px-4 py-2 text-center outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35"
        aria-label="跳转到输入要求"
        @click="emit('focusPromptInput')"
      >
        <span class="mx-auto grid size-12 place-items-center rounded-2xl bg-slate-950 text-white transition group-hover:bg-[#315be8] dark:bg-white dark:text-slate-950 dark:group-hover:bg-stone-100">
          <Edit3 class="size-5" />
        </span>
        <span class="mt-4 block text-lg font-semibold text-slate-950 transition group-hover:text-[#315be8] dark:text-stone-50">输入需求后开始生成</span>
      </button>
      <div class="mx-auto mt-5 flex max-w-[760px] flex-wrap justify-center gap-2">
        <button
          v-for="item in promptSuggestions"
          :key="item.label"
          type="button"
          class="studio-button rounded-xl bg-white px-3 py-2 text-[13px] font-medium text-slate-700 shadow-sm ring-1 ring-black/[0.06] transition hover:bg-[#4F7CFF]/[0.08] hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/30 dark:bg-white/[0.06] dark:text-stone-200 dark:ring-white/10 dark:hover:bg-white/[0.1]"
          @click="emit('applyPromptSuggestion', item.prompt)"
        >
          {{ item.label }}
        </button>
      </div>
    </div>
  </div>

  <div v-else class="mx-auto flex w-full max-w-[920px] flex-col gap-5 pb-2">
    <div class="mx-auto rounded-full bg-white px-3 py-1.5 text-xs font-medium text-slate-500 shadow-sm dark:bg-white/[0.08] dark:text-stone-300">
      {{ conversation.turns.length }} 轮 · {{ formatConversationTime(conversation.updatedAt) }}
    </div>

    <template v-for="turn in conversation.turns" :key="turn.id">
      <article v-if="isTurnCollapsed(turn)" class="flex items-start gap-3">
        <div class="mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-black/[0.06] dark:bg-white/[0.08] dark:ring-white/10 sm:size-9" aria-label="家可美头像">
          <img src="/jiakemei-mark.svg" alt="" class="size-5 rounded-lg sm:size-6" loading="lazy" decoding="async" />
        </div>
        <button type="button" class="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-dashed border-black/[0.08] bg-white px-4 py-3 text-left text-sm text-slate-600 shadow-sm transition hover:border-[#4F7CFF]/25 hover:bg-[#F8FAFC] dark:border-white/10 dark:bg-[#171a21] dark:text-stone-300 dark:hover:bg-white/[0.06]" @click="toggleTurnExpanded(turn.id)">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="font-semibold text-slate-800 dark:text-stone-100">已折叠较早轮次</span>
            <span class="text-xs text-slate-400">{{ statusLabel(turn) }} · {{ successImages(turn).length }}/{{ turn.images.length }} 张</span>
          </div>
          <div v-if="previewImages(turn).length" class="mt-3 flex gap-2 overflow-hidden">
            <img v-for="image in previewImages(turn)" :key="image.id" :src="imageSrc(image)" :alt="image.sourceName || '生成图片预览'" class="size-12 shrink-0 rounded-xl object-cover" loading="lazy" decoding="async" />
          </div>
        </button>
      </article>

      <template v-else>
      <article class="flex items-start justify-end gap-2 sm:gap-3">
        <div class="max-w-[calc(100%-44px)] rounded-2xl rounded-tr-md bg-slate-950 px-4 py-3 text-white shadow-sm dark:bg-white dark:text-slate-950 sm:max-w-[min(720px,84%)]">
          <p v-if="turn.prompt" class="whitespace-pre-wrap text-sm leading-6">{{ turn.prompt }}</p>
          <div v-else class="text-sm text-white/60 dark:text-slate-500">提示词已删除</div>
          <div v-if="turn.referenceImages.length" class="mt-3 flex gap-2 overflow-x-auto">
            <button
              v-for="(image, index) in turn.referenceImages"
              :key="`${image.name}-${referenceSrc(image).slice(-12)}-${index}`"
              type="button"
              class="group/reference relative size-14 shrink-0 overflow-hidden rounded-xl border border-white/15 bg-white/5 outline-none transition hover:border-white/35 focus-visible:ring-2 focus-visible:ring-white/45"
              :aria-label="`放大查看参考图 ${index + 1}`"
              @click="openReferenceLightbox(turn, index)"
            >
              <img :src="referenceSrc(image)" alt="参考图" class="h-full w-full cursor-zoom-in object-cover transition duration-200 group-hover/reference:scale-[1.04]" loading="lazy" decoding="async" />
            </button>
          </div>
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/10 pt-2 text-[11px] text-white/60 dark:border-slate-900/10 dark:text-slate-500">
            <span>{{ turn.mode === 'edit' ? '图生图' : '文生图' }} · {{ modelLabel(turn.model) }} · {{ turn.count }} 张 · {{ turn.size }}</span>
            <span class="flex items-center gap-1">
              <button type="button" class="inline-flex size-7 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="复制 Prompt" @click="copyPrompt(turn.prompt)">
                <Copy class="size-3.5" />
              </button>
              <button type="button" class="inline-flex size-7 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="复用配置" @click="emit('reuseTurnConfig', turn.id)">
                <RefreshCw class="size-3.5" />
              </button>
              <button type="button" class="inline-flex size-7 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="删除提示词" @click="emit('deletePrompt', turn.id)">
                <Trash2 class="size-3.5" />
              </button>
            </span>
          </div>
        </div>
        <div class="mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-slate-950 text-xs font-semibold text-white shadow-sm ring-1 ring-white/60 dark:bg-white dark:text-slate-950 dark:ring-white/10 sm:size-9" :title="displayUserName" aria-label="用户头像">
          <img v-if="props.userAvatarUrl" :src="props.userAvatarUrl" alt="" class="h-full w-full object-cover" loading="lazy" decoding="async" />
          <span v-else>{{ displayUserInitial }}</span>
        </div>
      </article>

      <article class="flex items-start gap-3">
        <div class="mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-black/[0.06] dark:bg-white/[0.08] dark:ring-white/10 sm:size-9" aria-label="家可美头像">
          <img src="/jiakemei-mark.svg" alt="" class="size-5 rounded-lg sm:size-6" loading="lazy" decoding="async" />
        </div>
        <div class="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-black/[0.06] bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#171a21] sm:p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-full px-2.5 py-1 text-[11px] font-semibold" :class="statusClass(turn)">{{ statusLabel(turn) }}</span>
              <span class="inline-flex items-center gap-1 text-xs text-slate-500 dark:text-stone-400">
                <Clock3 class="size-3.5" />
                {{ turn.error || (turn.status === 'success' ? '结果已保存到图库' : '任务状态自动更新') }}
              </span>
            </div>
            <button v-if="turn.status === 'queued' || turn.status === 'generating'" type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:bg-rose-400/10 dark:text-rose-300" @click="emit('cancelTurn', turn.id)">
              <XCircle class="size-3.5" />
              中止
            </button>
          </div>

          <div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <div v-for="(image, index) in turn.images" :key="image.id" class="group relative aspect-square overflow-hidden rounded-2xl border border-black/[0.06] bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04]" data-testid="generated-image-card">
              <img v-if="image.status === 'success' && imageSrc(image)" :src="imageSrc(image)" :alt="image.sourceName || '生成图片'" class="h-full w-full cursor-zoom-in object-cover transition duration-300 group-hover:scale-[1.02]" loading="lazy" decoding="async" data-testid="generated-image" @click="emit('openLightbox', imageItems(turn), imageItems(turn).findIndex((item) => item.id === image.id))" />
              <div v-else-if="image.status === 'loading'" class="flex h-full flex-col items-center justify-center gap-3 p-4 text-center">
                <div class="studio-skeleton absolute inset-0 opacity-60" />
                <LoaderCircle class="relative size-6 animate-spin text-[#4F7CFF]" />
                <div class="relative text-xs font-medium text-slate-500 dark:text-stone-400">
                  {{ image.taskStatus === 'running' ? '生成中' : image.taskStatus === 'queued' ? '排队中' : image.progress?.includes('参考图') ? '准备中' : '等待入队' }}
                  <span v-if="image.progress" class="mt-1 block">{{ image.progress }}</span>
                  <span class="mt-1 block">{{ elapsed(image) }}</span>
                </div>
              </div>
              <div v-else-if="image.status === 'error'" class="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
                <XCircle class="size-7 text-rose-500" />
                <span class="line-clamp-2 text-xs text-rose-600 dark:text-rose-300">{{ image.error || '生成失败' }}</span>
                <button type="button" class="studio-button inline-flex items-center gap-1 rounded-xl bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:bg-rose-400/10 dark:text-rose-300" @click="emit('retryImage', turn.id, image.id)">
                  <RefreshCw class="size-3" />
                  重试
                </button>
              </div>
              <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-center text-slate-400">
                <XCircle class="size-7" />
                <span class="text-xs">任务已中止</span>
              </div>
              <div v-if="image.status === 'success'" class="absolute inset-x-2 bottom-2 flex items-center justify-center gap-1 opacity-0 transition group-hover:opacity-100">
                <button type="button" class="inline-flex size-8 items-center justify-center rounded-xl bg-white/90 text-slate-700 shadow-sm" title="下载" @click.stop="downloadImage(image)">
                  <Download class="size-4" />
                </button>
                <button type="button" class="inline-flex size-8 items-center justify-center rounded-xl bg-white/90 text-slate-700 shadow-sm" title="继续编辑" @click.stop="emit('continueEdit', image)">
                  <Edit3 class="size-4" />
                </button>
              </div>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap justify-end gap-1 border-t border-black/[0.06] pt-3 dark:border-white/10">
            <button
              v-if="turn.images.some((image) => image.status === 'success')"
              type="button"
              class="studio-button inline-flex min-w-[108px] items-center justify-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]"
              :disabled="downloadingTurnId !== null"
              :aria-busy="downloadingTurnId === turn.id"
              @click="downloadZip(turn)"
            >
              <LoaderCircle v-if="downloadingTurnId === turn.id" class="size-3.5 animate-spin" />
              <Archive v-else class="size-3.5" />
              {{ downloadingTurnId === turn.id ? '正在打包' : '下载 ZIP' }}
            </button>
            <button type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]" @click="emit('regenerateTurn', turn.id)">
              <RefreshCw class="size-3.5" />
              重新生成
            </button>
            <button v-if="turn.images.some((image) => image.status === 'error')" type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]" @click="emit('dismissErrors', turn.id)">
              <Check class="size-3.5" />
              忽略失败
            </button>
            <button type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-rose-600 hover:bg-rose-50" @click="emit('deleteResults', turn.id)">
              <Trash2 class="size-3.5" />
              删除结果
            </button>
          </div>

          <div v-if="timeoutRetry && turn.images.some((image) => image.taskId === timeoutRetry?.taskId)" class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-200">
            <span>{{ timeoutRetry.taskError }}，可以继续等待或结束该图片。</span>
            <div class="flex gap-2">
              <button v-if="allowTimeoutRetryContinue" type="button" class="rounded-xl bg-amber-600 px-3 py-1.5 font-semibold text-white" @click="emit('timeoutRetryContinue')">继续等待</button>
              <button type="button" class="rounded-xl border border-amber-300 px-3 py-1.5 font-semibold" @click="emit('timeoutRetryCancel')">结束任务</button>
            </div>
          </div>
        </div>
      </article>
      </template>
    </template>
  </div>
</template>
