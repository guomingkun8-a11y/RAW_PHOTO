<script setup lang="ts">
import { ChevronLeft, ChevronRight, Download, RotateCcw, X, ZoomIn, ZoomOut } from "@lucide/vue";
import { computed, onBeforeUnmount, watch } from "vue";

export type LightboxImage = { id: string; src: string; name?: string };

const props = withDefaults(defineProps<{
  images: LightboxImage[];
  open: boolean;
  currentIndex: number;
}>(), { images: () => [], open: false, currentIndex: 0 });
const emit = defineEmits<{ close: []; change: [index: number] }>();
const scale = defineModel<number>("scale", { default: 1 });

const current = computed(() => props.images[Math.min(props.images.length - 1, Math.max(0, props.currentIndex))]);

function close() { emit("close"); }
function previous() { if (props.images.length) emit("change", (props.currentIndex - 1 + props.images.length) % props.images.length); }
function next() { if (props.images.length) emit("change", (props.currentIndex + 1) % props.images.length); }
function reset() { scale.value = 1; }
function download() {
  if (!current.value) return;
  const link = document.createElement("a");
  link.href = current.value.src;
  link.download = current.value.name || `image-${props.currentIndex + 1}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
function onKey(event: KeyboardEvent) {
  if (!props.open) return;
  if (event.key === "Escape") close();
  if (event.key === "ArrowLeft") previous();
  if (event.key === "ArrowRight") next();
}

watch(() => props.open, (open) => {
  if (open) window.addEventListener("keydown", onKey);
  else window.removeEventListener("keydown", onKey);
}, { immediate: true });
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div v-if="open && current" class="fixed inset-0 z-[130] flex flex-col bg-slate-950/95 text-white" @mousedown.self="close">
      <header class="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div class="text-sm text-white/70">{{ currentIndex + 1 }} / {{ images.length }}</div>
        <div class="flex items-center gap-1">
          <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-xl text-white/75 hover:bg-white/10 hover:text-white" title="缩小" @click="scale = Math.max(0.5, scale - 0.25)"><ZoomOut class="size-4" /></button>
          <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-xl text-white/75 hover:bg-white/10 hover:text-white" title="放大" @click="scale = Math.min(3, scale + 0.25)"><ZoomIn class="size-4" /></button>
          <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-xl text-white/75 hover:bg-white/10 hover:text-white" title="重置缩放" @click="reset"><RotateCcw class="size-4" /></button>
          <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-xl text-white/75 hover:bg-white/10 hover:text-white" title="下载" @click="download"><Download class="size-4" /></button>
          <button type="button" class="studio-button ml-2 inline-flex size-9 items-center justify-center rounded-xl text-white/75 hover:bg-white/10 hover:text-white" aria-label="关闭" @click="close"><X class="size-5" /></button>
        </div>
      </header>
      <div class="relative min-h-0 flex-1 overflow-auto p-5 sm:p-10">
        <img :src="current.src" :alt="current.name || '生成图片'" class="mx-auto block max-h-full max-w-full origin-center object-contain transition-transform duration-200" :style="{ transform: `scale(${scale})` }" />
        <button v-if="images.length > 1" type="button" class="absolute left-3 top-1/2 inline-flex size-11 -translate-y-1/2 items-center justify-center rounded-2xl bg-white/10 text-white hover:bg-white/20" aria-label="上一张" @click="previous"><ChevronLeft class="size-6" /></button>
        <button v-if="images.length > 1" type="button" class="absolute right-3 top-1/2 inline-flex size-11 -translate-y-1/2 items-center justify-center rounded-2xl bg-white/10 text-white hover:bg-white/20" aria-label="下一张" @click="next"><ChevronRight class="size-6" /></button>
      </div>
    </div>
  </Teleport>
</template>
