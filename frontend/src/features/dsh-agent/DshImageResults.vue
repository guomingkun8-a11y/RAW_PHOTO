<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ChevronLeft, ChevronRight, CircleAlert, Download, FileText, Image, LoaderCircle, Pencil, RefreshCw } from "@lucide/vue";
import { toast } from "vue-sonner";
import { downloadImageTaskZip } from "@/lib/api";
import { formatImageModel } from "@/lib/image-models";
import DshImagePreview from "./DshImagePreview.vue";
import DshImageGeneration from "./DshImageGeneration.vue";
import DshDiagramArtifact from "./DshDiagramArtifact.vue";
import type { DshImageMedia } from "./api";
const props = defineProps<{ media: DshImageMedia[]; readonly?: boolean }>();
const emit = defineEmits<{ edit: [reference: string] }>();
const downloading = ref(false);
const selected = ref(0);
const previewKey = ref<string | null>(null);
const imageStates = ref<Record<string, "loaded" | "error">>({});
const dimensions = ref<Record<string, number>>({});
const attachments = computed(() => props.media.filter(item => item.kind === "reference" || item.kind === "file"));
const diagrams = computed(() => props.media.filter(item => item.kind === "diagram"));
const tasks = computed(() => props.media.filter(item => item.kind === "task"));
const isPending = (item: DshImageMedia) => ["queued", "pending", "running"].includes(item.task?.status || "");
const labels: Record<string, string> = { queued: "排队中", pending: "排队中", running: "图片生成中", success: "生成完成", error: "生成失败", canceled: "已取消", unconfirmed: "提交状态待确认" };
// Keep a slot per requested image, including separate tool calls in this reply.
const slides = computed(() => tasks.value.flatMap(item => {
  const outputs = item.task?.data || [];
  const count = Math.max(outputs.length, isPending(item) ? Math.min(8, item.task?.image_count || 1) : 1);
  return Array.from({ length: count }, (_, imageIndex) => ({
    key: `${item.media_id}:${imageIndex}`, item, imageIndex, url: outputs[imageIndex]?.url || "",
  }));
}));
const current = computed(() => slides.value[selected.value]);
const stackLayers = computed(() => Array.from({ length: Math.min(2, slides.value.length - 1) }, (_, index) => slides.value[(selected.value + index + 1) % slides.value.length]!));
const generated = computed(() => slides.value.filter(slide => slide.url));
const gallery = computed(() => [
  ...attachments.value.filter(item => item.kind === "reference" && item.url).map(item => ({ key: item.media_id, url: item.url!, label: item.filename || "参考图片", item, imageIndex: 0 })),
  ...slides.value.flatMap((slide, index) => slide.url ? [{ ...slide, label: `生成图片 ${index + 1}` }] : []),
]);
const previewIndex = computed(() => gallery.value.findIndex(image => image.key === previewKey.value));
const pending = computed(() => tasks.value.some(isPending));
const summary = computed(() => {
  if (pending.value) return generated.value.length ? `${generated.value.length} / ${slides.value.length} 张已生成` : tasks.value.some(item => item.task?.status === "running") ? "图片生成中" : "排队中";
  if (tasks.value.some(item => item.task?.status === "error")) return generated.value.length ? "部分生成失败" : "生成失败";
  if (tasks.value.some(item => !item.task || item.task.status === "unconfirmed")) return "提交状态待确认";
  if (tasks.value.some(item => item.task?.status === "canceled")) return generated.value.length ? "部分已取消" : "已取消";
  return generated.value.length ? "生成完成" : "未返回图片";
});
const ratio = computed(() => {
  if (current.value?.url && dimensions.value[current.value.url]) return dimensions.value[current.value.url]!;
  const match = current.value?.item.task?.size?.match(/^(\d+)x(\d+)$/);
  return match && Number(match[1]) > 0 && Number(match[2]) > 0 ? Number(match[1]) / Number(match[2]) : 1;
});
const frameStyle = computed(() => {
  const frameRatio = Math.max(.5, Math.min(2, ratio.value));
  return { "--result-width": `${Math.min(360, 360 * frameRatio)}px`, "--image-ratio": String(frameRatio) };
});

watch(slides, (next, previous) => {
  const key = previous?.[selected.value]?.key;
  const index = next.findIndex(slide => slide.key === key);
  selected.value = index >= 0 ? index : Math.max(0, Math.min(selected.value, next.length - 1));
});

function imageChanged(event: Event) {
  const image = event.target as HTMLImageElement;
  const url = image.dataset.source!;
  if (image.naturalWidth && image.naturalHeight) dimensions.value[url] = image.naturalWidth / image.naturalHeight;
  imageStates.value[url] = event.type === "load" ? "loaded" : "error";
}

function selectPreview(index: number) {
  const image = gallery.value[index];
  if (!image) return;
  previewKey.value = image.key;
  const position = slides.value.findIndex(slide => slide.key === image.key);
  if (position >= 0) selected.value = position;
}

async function download(images = generated.value) {
  if (downloading.value || !images.length) return;
  downloading.value = true;
  try {
    const blob = await downloadImageTaskZip({ folderName: "AI-images", items: images.map(image => ({ taskId: image.item.task_id, imageIndex: image.imageIndex, filename: `${image.item.media_id}-${image.imageIndex + 1}.png` })) });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "AI-images.zip";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  catch (error) { toast.error(error instanceof Error ? error.message : "下载失败"); }
  finally { downloading.value = false; }
}
</script>

<template>
  <div class="image-media">
    <DshDiagramArtifact v-for="item in diagrams" :key="item.media_id" :media="item" :readonly="readonly" />
    <template v-for="item in attachments" :key="item.media_id">
      <button v-if="item.kind === 'reference'" type="button" class="image-reference" :aria-label="`预览${item.filename || '参考图片'}`" @click="previewKey = item.media_id"><img :src="item.url" :alt="item.filename || '参考图片'" loading="lazy" /><span>{{ item.filename || '参考图片' }}</span></button>
      <div v-else class="file-reference"><FileText class="size-5" /><div><strong>{{ item.filename }}</strong><small>{{ item.format?.toUpperCase() }} · {{ Math.max(1, Math.round((item.size_bytes || 0) / 1024)) }} KB<span v-if="item.truncated"> · 已截取前 {{ item.text_chars }} 字</span></small></div></div>
    </template>
    <section v-if="current" class="image-result" aria-label="生成图片" :data-status="pending ? 'running' : current.item.task?.status" :style="frameStyle">
      <header><strong>{{ formatImageModel(current.item.model || '') }}</strong><span role="status"><LoaderCircle v-if="pending" class="size-3.5 animate-spin" />{{ summary }}</span></header>
      <div class="image-stack" :class="{ 'has-stack': slides.length > 1 }">
        <div v-for="(slide, index) in stackLayers" :key="slide.key" class="image-stack-layer" :style="{ '--stack-depth': index + 1, zIndex: 2 - index }" aria-hidden="true">
          <img v-if="slide.url && imageStates[slide.url] !== 'error'" :src="slide.url" :data-source="slide.url" alt="" loading="lazy" @load="imageChanged" @error="imageChanged" />
        </div>
        <div class="image-stage" :aria-busy="isPending(current.item) || !!current.url && !imageStates[current.url]">
          <button v-if="current.url && imageStates[current.url] !== 'error'" class="image-open" type="button" :aria-label="`放大生成图片 ${selected + 1}`" :disabled="imageStates[current.url] !== 'loaded'" @click="previewKey = current.key">
            <img :key="current.url" :src="current.url" :data-source="current.url" :alt="`生成图片 ${selected + 1}`" :class="{ 'is-loaded': imageStates[current.url] === 'loaded' }" @load="imageChanged" @error="imageChanged" />
          </button>
          <DshImageGeneration v-if="!current.url && isPending(current.item) || current.url && !imageStates[current.url]" :state="current.url ? 'loading' : current.item.task?.status === 'running' ? 'running' : 'queued'" />
          <div v-else-if="!current.url || imageStates[current.url] === 'error'" class="image-placeholder" role="status">
            <CircleAlert v-if="current.url && imageStates[current.url] === 'error' || ['error', 'unconfirmed'].includes(current.item.task?.status || '')" class="size-7" />
            <Image v-else class="size-8" />
            <strong>{{ current.url ? imageStates[current.url] === 'error' ? '图片加载失败' : '正在加载图片' : current.item.task?.status === 'success' ? '未返回图片' : labels[current.item.task?.status || 'unconfirmed'] || '图片暂不可用' }}</strong>
            <p v-if="!current.url">{{ current.item.task?.error || (current.item.task?.status === 'success' ? '任务未返回可展示的图片' : current.item.task?.status === 'canceled' ? '本次图片任务已取消' : '请勿重复提交，请查看任务状态') }}</p>
            <button v-if="current.url && imageStates[current.url] === 'error'" type="button" class="image-reload" @click="delete imageStates[current!.url]"><RefreshCw class="size-3.5" />重新加载</button>
          </div>
        </div>
      </div>
      <nav v-if="slides.length > 1" class="image-pagination" aria-label="生成图片翻页">
        <button type="button" aria-label="上一张生成图片" title="上一张" :disabled="selected === 0" @click="selected--"><ChevronLeft class="size-4" /></button>
        <output aria-label="图片页码" aria-live="polite">{{ selected + 1 }} <span>/</span> {{ slides.length }}</output>
        <button type="button" aria-label="下一张生成图片" title="下一张" :disabled="selected === slides.length - 1" @click="selected++"><ChevronRight class="size-4" /></button>
      </nav>
      <div v-if="!readonly && (current.url || generated.length)" class="image-actions">
        <button v-if="current.url" class="image-edit" type="button" @click="emit('edit', `${current.item.media_id}:${current.imageIndex}`)"><Pencil class="size-3.5" />继续修改</button>
        <button v-if="generated.length" type="button" aria-label="下载生成图片" :title="generated.length > 1 ? '下载全部已生成图片' : '下载生成图片'" :disabled="downloading" @click="download()"><LoaderCircle v-if="downloading" class="size-3.5 animate-spin" /><Download v-else class="size-3.5" />{{ generated.length > 1 ? '下载全部' : '下载' }}</button>
      </div>
      <details v-if="current.item.prompt"><summary>生成提示词</summary><p>{{ current.item.prompt }}</p></details>
    </section>
    <DshImagePreview v-if="previewIndex >= 0" :images="gallery" :index="previewIndex" :downloadable="!readonly && gallery[previewIndex]?.item.kind === 'task'" :downloading="downloading" @close="previewKey = null" @select="selectPreview" @download="download([gallery[previewIndex]!])" />
  </div>
</template>

<style scoped>
.image-media { display: flex; flex-wrap: wrap; gap: 10px; min-width: 0; }
.image-reference { display: grid; width: 100px; gap: 4px; padding: 0; border: 0; background: transparent; color: var(--muted-foreground); font-size: 11px; text-align: left; cursor: zoom-in; }
.image-reference img { width: 100px; height: 82px; object-fit: contain; border: 1px solid var(--input); border-radius: 6px; background: var(--studio-bg); }
.image-reference span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-reference { display: flex; align-items: center; gap: 10px; min-width: 0; max-width: 300px; padding: 10px 12px; border: 1px solid var(--input); border-radius: 6px; color: var(--foreground); }
.file-reference > svg { flex: none; color: var(--muted-foreground); }
.file-reference > div { display: grid; gap: 3px; min-width: 0; }
.file-reference strong { font-size: 12px; font-weight: 500; overflow-wrap: anywhere; }
.file-reference small { color: var(--muted-foreground); font-size: 10px; }
.image-result { width: 100%; max-width: var(--result-width); min-width: 0; padding-block: 6px; }
.image-result header { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 12px; margin-bottom: 10px; font-size: 12px; color: var(--muted-foreground); }
.image-result header strong { margin-right: auto; font-weight: 500; overflow-wrap: anywhere; }
.image-result header span { display: inline-flex; align-items: center; gap: 5px; }
.image-stack { position: relative; isolation: isolate; }
.image-stack.has-stack { padding: 14px 18px 0 0; perspective: 900px; }
.image-stack-layer { position: absolute; inset: 14px 18px 0 0; overflow: hidden; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--secondary); transform-origin: center bottom; transform: translate(calc(var(--stack-depth) * 6px), calc(var(--stack-depth) * -4px)) rotate(calc(var(--stack-depth) * 2deg)) rotateY(-3deg) scale(calc(1 - var(--stack-depth) * .025)); box-shadow: 0 2px 6px rgb(0 0 0 / .12); pointer-events: none; }
.image-stack-layer img { display: block; width: 100%; height: 100%; object-fit: cover; }
.image-stack.has-stack .image-stage { z-index: 3; box-shadow: 0 3px 8px rgb(0 0 0 / .14); }
.image-result button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; border: 0; border-radius: 5px; padding: 6px 8px; color: var(--muted-foreground); background: transparent; font-size: 12px; cursor: pointer; }
.image-result button:hover:not(:disabled) { background: var(--secondary); color: var(--foreground); }
.image-result button:disabled { opacity: .4; cursor: default; }
.image-media button:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.image-stage { position: relative; width: 100%; aspect-ratio: var(--image-ratio); overflow: hidden; border-radius: 8px; background: var(--secondary); }
.image-stage .image-open { display: block; width: 100%; height: 100%; padding: 0; background: transparent; cursor: zoom-in; }
.image-stage .image-open:disabled { opacity: 1; cursor: default; }
.image-stage img { display: block; width: 100%; height: 100%; object-fit: contain; opacity: 0; filter: blur(8px); transition: opacity 420ms ease-out, filter 600ms ease-out; }
.image-stage img.is-loaded { opacity: 1; filter: blur(0); }
.image-placeholder { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 20px; color: var(--muted-foreground); text-align: center; }
.image-placeholder > svg { opacity: .65; flex: none; }
.image-placeholder strong { color: var(--foreground); font-size: 14px; font-weight: 500; }
.image-placeholder small { font-size: 12px; }
.image-placeholder p { max-height: 90px; overflow: auto; margin: 0; }
.image-pagination { display: flex; align-items: center; justify-content: center; gap: 7px; margin-top: 8px; }
.image-pagination button { width: 30px; height: 30px; padding: 0; }
.image-pagination output { min-width: 44px; color: var(--foreground); font-size: 12px; text-align: center; font-variant-numeric: tabular-nums; }
.image-pagination output span { margin-inline: 5px; color: var(--muted-foreground); }
.image-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; }
.image-result p { font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; white-space: pre-wrap; }
.image-result[data-status='error'] header span { color: var(--destructive); }
.image-result details { margin-top: 10px; color: var(--muted-foreground); font-size: 12px; }
.image-result summary { width: fit-content; cursor: pointer; }
@media (pointer: coarse) { .image-result button { min-height: 40px; } .image-pagination button { width: 40px; } }
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } .image-stage img { transition: none; } }
</style>
