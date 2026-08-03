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
      <div class="mx-auto grid size-12 place-items-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
        <Edit3 class="size-5" />
      </div>
      <h2 class="mt-4 text-lg font-semibold text-slate-950 dark:text-stone-50">输入需求后开始生成</h2>
      <p class="mx-auto mt-2 max-w-[520px] text-sm leading-6 text-slate-500 dark:text-stone-400">Prompt、参考图和生成结果会像对话一样按轮次保存在这里。</p>
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
            <img v-for="(image, index) in turn.referenceImages" :key="`${image.name}-${referenceSrc(image).slice(-12)}-${index}`" :src="referenceSrc(image)" alt="参考图" class="size-14 shrink-0 rounded-xl border border-white/15 object-cover" loading="lazy" decoding="async" />
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
