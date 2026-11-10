<script setup lang="ts">
import { Download, Expand, FileCode2, LoaderCircle, Network } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { toast } from "vue-sonner";
import DshImagePreview from "./DshImagePreview.vue";
import type { DshImageMedia } from "./api";

const props = defineProps<{ media: DshImageMedia; readonly?: boolean }>();
const imageUrl = ref("");
const previewOpen = ref(false);
const exporting = ref(false);
const loaded = ref(false);
const error = ref("");
const expandButton = ref<HTMLButtonElement>();
const title = computed(() => props.media.title || "图示");
const filename = computed(() => title.value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 80) || "diagram");

function clearImage() { if (imageUrl.value) URL.revokeObjectURL(imageUrl.value); imageUrl.value = ""; }
watch(() => props.media.svg, (svg) => {
  clearImage();
  loaded.value = false;
  error.value = "";
  previewOpen.value = false;
  // SVG is validated by the server and used as an inert image, never inline HTML.
  if (svg) imageUrl.value = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  else error.value = "图示原稿不可用";
}, { immediate: true });

function save(blob: Blob, extension: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filename.value}.${extension}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadSvg() { if (props.media.svg) save(new Blob([props.media.svg], { type: "image/svg+xml;charset=utf-8" }), "svg"); }
function downloadHtml() {
  if (!props.media.svg) return;
  const escapedTitle = title.value.replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
  const data = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(props.media.svg)}`;
  save(new Blob([`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><title>${escapedTitle}</title><style>body{margin:0;padding:24px;background:#f5f5f5;font-family:system-ui,sans-serif}img{display:block;max-width:100%;height:auto;margin:auto}</style><img src="${data}" alt="${escapedTitle}"></html>`], { type: "text/html;charset=utf-8" }), "html");
}
async function downloadPng() {
  if (!imageUrl.value || !loaded.value || exporting.value) return;
  exporting.value = true;
  try {
    const image = new Image();
    image.src = imageUrl.value;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(4096, Math.max(1, props.media.width || image.naturalWidth));
    canvas.height = Math.min(4096, Math.max(1, props.media.height || image.naturalHeight));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas unavailable");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error("PNG export failed")), "image/png"));
    save(blob, "png");
  } catch { toast.error("PNG 导出失败，仍可下载 SVG 原稿"); }
  finally { exporting.value = false; }
}
function closePreview() { previewOpen.value = false; expandButton.value?.focus({ preventScroll: true }); }
onBeforeUnmount(clearImage);
</script>

<template>
  <section class="dsh-artifact" :aria-label="`图示：${title}`">
    <header>
      <strong><Network class="size-4" />{{ title }}</strong>
      <div class="dsh-artifact-actions">
        <button v-if="!readonly" type="button" aria-label="下载图示 HTML" title="下载 HTML" :disabled="!loaded" @click="downloadHtml"><FileCode2 class="size-4" /></button>
        <button v-if="!readonly" type="button" aria-label="下载图示 SVG" title="下载 SVG" :disabled="!loaded" @click="downloadSvg"><Download class="size-4" /><span>SVG</span></button>
        <button v-if="!readonly" type="button" aria-label="下载图示 PNG" title="下载 PNG" :disabled="!loaded || exporting" @click="downloadPng"><LoaderCircle v-if="exporting" class="size-4 animate-spin" /><Download v-else class="size-4" /><span>PNG</span></button>
        <button ref="expandButton" type="button" aria-label="放大图示" title="放大图示" :disabled="!loaded" @click="previewOpen = true"><Expand class="size-4" /></button>
      </div>
    </header>
    <p v-if="error" class="dsh-artifact-error" role="status">{{ error }}</p>
    <button v-else class="dsh-artifact-image" type="button" :aria-label="`预览图示 ${title}`" :disabled="!loaded" @click="previewOpen = true">
      <img :src="imageUrl" :alt="title" @load="loaded = true" @error="error = '图示暂时无法显示，请重新打开对话'" />
    </button>
    <DshImagePreview v-if="previewOpen && imageUrl" :images="[{ url: imageUrl, label: title }]" :index="0" media-label="图示" :downloadable="!readonly" @close="closePreview" @download="downloadSvg" />
  </section>
</template>

<style scoped>
.dsh-artifact { min-width: 0; width: 100%; overflow: hidden; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); }
.dsh-artifact header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px; padding: 6px 8px 6px 12px; border-bottom: 1px solid var(--studio-border); }
.dsh-artifact strong { display: flex; min-width: 0; flex: 1 1 130px; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.dsh-artifact strong svg { flex: none; }
.dsh-artifact-actions { display: flex; align-items: center; gap: 2px; margin-left: auto; }
.dsh-artifact-actions button { display: flex; min-width: 32px; height: 34px; align-items: center; justify-content: center; gap: 4px; padding: 0 6px; border: 0; border-radius: 4px; background: transparent; color: var(--muted-foreground); font-size: 11px; cursor: pointer; }
.dsh-artifact-actions button:hover:not(:disabled) { background: var(--studio-hover); color: var(--foreground); }
.dsh-artifact button:focus-visible { outline: 2px solid var(--ring); outline-offset: -2px; }
.dsh-artifact button:disabled { opacity: .5; cursor: default; }
.dsh-artifact-image { display: block; width: 100%; padding: 12px; border: 0; background: transparent; cursor: zoom-in; }
.dsh-artifact-image img { display: block; width: 100%; max-height: 440px; object-fit: contain; }
.dsh-artifact-error { margin: 0; padding: 20px; color: var(--muted-foreground); font-size: 13px; }
@media (pointer: coarse) { .dsh-artifact-actions button { min-width: 40px; height: 44px; } }
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } }
</style>
