<script setup lang="ts">
import { ChevronLeft, ChevronRight, Download, Minus, Plus, RotateCcw, X } from "@lucide/vue";
import { nextTick, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{ images: { url: string; label: string }[]; index: number; downloadable?: boolean; downloading?: boolean; mediaLabel?: string }>(), { mediaLabel: "图片" });
const emit = defineEmits<{ close: []; select: [index: number]; download: [] }>();
const dialog = ref<HTMLDialogElement>();
const zoom = ref(1);
const viewport = ref<HTMLElement>();
onMounted(() => dialog.value?.showModal());
watch(() => props.index, async () => { zoom.value = 1; await nextTick(); viewport.value?.scrollTo(0, 0); });
function navigate(event: KeyboardEvent) {
  if (event.key === "ArrowLeft" && props.index > 0) emit("select", props.index - 1);
  if (event.key === "ArrowRight" && props.index < props.images.length - 1) emit("select", props.index + 1);
}
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="dsh-image-preview" :aria-label="`${mediaLabel}预览`" @close="emit('close')" @keydown="navigate">
      <header>
        <span>{{ images[index]?.label }} <small>{{ index + 1 }} / {{ images.length }}</small></span>
        <button v-if="downloadable" type="button" :aria-label="mediaLabel === '图片' ? '下载原图' : '下载图表 SVG'" :title="mediaLabel === '图片' ? '下载原图' : '下载 SVG'" :disabled="downloading" @click="emit('download')"><Download class="size-4" /></button>
        <button type="button" :aria-label="`关闭${mediaLabel}预览`" title="关闭" autofocus @click="dialog?.close()"><X class="size-5" /></button>
      </header>
      <div ref="viewport" class="dsh-preview-viewport"><div class="dsh-preview-image" :style="{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }"><img :src="images[index]?.url" :alt="images[index]?.label" /></div></div>
      <div class="dsh-preview-controls">
      <nav v-if="mediaLabel === '图片' && images.length > 1" class="dsh-preview-thumbnails" aria-label="图片缩略图">
        <button v-for="(image, position) in images" :key="`${position}:${image.url}`" type="button" :aria-label="`查看第 ${position + 1} 张图片`" :aria-current="position === index ? 'true' : undefined" @click="emit('select', position)"><img :src="image.url" :alt="image.label" loading="lazy" /></button>
      </nav>
      <footer>
        <button v-if="mediaLabel === '图片'" type="button" aria-label="上一张图片" title="上一张" :disabled="index === 0" @click="emit('select', index - 1)"><ChevronLeft class="size-5" /></button>
        <button type="button" :aria-label="`缩小${mediaLabel}`" title="缩小" :disabled="zoom <= 1" @click="zoom = Math.max(1, zoom - .5)"><Minus class="size-4" /></button>
        <output>{{ Math.round(zoom * 100) }}%</output>
        <button type="button" :aria-label="`放大${mediaLabel}`" title="放大" :disabled="zoom >= 3" @click="zoom = Math.min(3, zoom + .5)"><Plus class="size-4" /></button>
        <button type="button" :aria-label="`重置${mediaLabel}缩放`" title="适应窗口" @click="zoom = 1"><RotateCcw class="size-4" /></button>
        <button v-if="mediaLabel === '图片'" type="button" aria-label="下一张图片" title="下一张" :disabled="index === images.length - 1" @click="emit('select', index + 1)"><ChevronRight class="size-5" /></button>
      </footer>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.dsh-image-preview { position: fixed; inset: 0; width: 100dvw; height: 100dvh; max-width: none; max-height: none; margin: 0; padding: 0; border: 0; background: #141618; color: #fafafa; }
.dsh-image-preview[open] { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; }
.dsh-image-preview::backdrop { background: rgb(0 0 0 / .8); }
header, footer { display: flex; align-items: center; gap: 8px; padding: 12px 18px; background: #1f2225; }
header > span { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
header small { margin-left: 10px; color: #bfc5cc; }
footer { justify-content: center; padding-bottom: max(12px, env(safe-area-inset-bottom)); }
button { display: grid; flex: none; place-items: center; width: 40px; height: 40px; border: 0; border-radius: 6px; background: transparent; color: inherit; cursor: pointer; }
button:hover:not(:disabled) { background: #363b42; }
button:focus-visible { outline: 2px solid #cbd5e1; outline-offset: -2px; }
button:disabled { opacity: .35; cursor: default; }
output { min-width: 46px; text-align: center; font-size: 12px; font-variant-numeric: tabular-nums; }
.dsh-preview-viewport { overflow: auto; min-height: 0; scrollbar-width: thin; }
.dsh-preview-image { display: grid; place-items: center; padding: 16px; }
img { display: block; width: 100%; height: 100%; min-width: 0; min-height: 0; object-fit: contain; }
.dsh-preview-controls { min-width: 0; background: #1f2225; }
.dsh-preview-thumbnails { display: flex; gap: 8px; width: fit-content; max-width: 100%; overflow-x: auto; margin: 0 auto; padding: 10px 18px 0; scrollbar-width: thin; }
.dsh-preview-thumbnails button { width: 48px; height: 48px; padding: 3px; border: 1px solid transparent; }
.dsh-preview-thumbnails button[aria-current='true'] { border-color: #fafafa; background: #363b42; }
.dsh-preview-thumbnails img { border-radius: 3px; }
</style>
