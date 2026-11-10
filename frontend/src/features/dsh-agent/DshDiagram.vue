<script setup lang="ts">
import { Check, Code2, Copy, Download, Expand, LoaderCircle, Network } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { toast } from "vue-sonner";
import DshImagePreview from "./DshImagePreview.vue";
import { diagramLabel, renderDiagram } from "./diagram";

const props = defineProps<{ code: string; streaming?: boolean }>();
const label = computed(() => diagramLabel(props.code));
const dark = ref(document.documentElement.classList.contains("dark"));
const imageUrl = ref("");
const error = ref("");
const rendering = ref(false);
const sourceOpen = ref(false);
const previewOpen = ref(false);
const copied = ref(false);
const expandButton = ref<HTMLButtonElement>();
let copyTimer: ReturnType<typeof setTimeout>;
const themeObserver = new MutationObserver(() => { dark.value = document.documentElement.classList.contains("dark"); });
onMounted(() => themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] }));

function clearImage() {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  imageUrl.value = "";
}

watch([() => props.code, () => props.streaming, dark], async ([code, streaming], _previous, onCleanup) => {
  const controller = new AbortController();
  onCleanup(() => controller.abort());
  error.value = "";
  previewOpen.value = false;
  clearImage();
  if (streaming) { rendering.value = false; return; }
  rendering.value = true;
  try {
    const svg = await renderDiagram(code, controller.signal);
    if (!controller.signal.aborted) imageUrl.value = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  } catch (cause) {
    if (!controller.signal.aborted) error.value = cause instanceof Error && cause.message.startsWith("图表") ? cause.message : "图表暂时无法渲染，已保留源码。";
  } finally {
    if (!controller.signal.aborted) rendering.value = false;
  }
}, { immediate: true });

async function copySource() {
  try {
    await navigator.clipboard.writeText(props.code);
    copied.value = true;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { copied.value = false; }, 2000);
  } catch { toast.error("复制失败，请检查浏览器剪贴板权限"); }
}

function download() {
  if (!imageUrl.value) return;
  const link = document.createElement("a");
  link.href = imageUrl.value;
  link.download = `${label.value}.svg`;
  link.click();
}

function closePreview() {
  previewOpen.value = false;
  expandButton.value?.focus({ preventScroll: true });
}

onBeforeUnmount(() => { themeObserver.disconnect(); clearTimeout(copyTimer); clearImage(); });
</script>

<template>
  <section class="dsh-diagram" :aria-label="label" :aria-busy="streaming || rendering">
    <header class="dsh-diagram-toolbar">
      <span class="dsh-diagram-label"><Network class="size-3.5" />{{ label }}</span>
      <div class="dsh-diagram-tools">
        <button type="button" :aria-label="sourceOpen ? '显示图表预览' : '查看图表源码'" :title="sourceOpen ? '显示预览' : '查看源码'" :aria-pressed="sourceOpen" @click="sourceOpen = !sourceOpen"><Network v-if="sourceOpen" class="size-4" /><Code2 v-else class="size-4" /></button>
        <button type="button" :aria-label="copied ? '已复制图表源码' : '复制图表源码'" :title="copied ? '已复制' : '复制源码'" @click="copySource"><Check v-if="copied" class="size-4" /><Copy v-else class="size-4" /></button>
        <button type="button" aria-label="下载图表 SVG" title="下载 SVG" :disabled="!imageUrl" @click="download"><Download class="size-4" /></button>
        <button ref="expandButton" type="button" aria-label="放大图表" title="放大图表" :disabled="!imageUrl" @click="previewOpen = true"><Expand class="size-4" /></button>
      </div>
    </header>
    <div v-if="sourceOpen || error" class="dsh-diagram-source"><p v-if="error" role="status">{{ error }}</p><pre><code>{{ code }}</code></pre></div>
    <div v-else-if="streaming || rendering" class="dsh-diagram-loading" role="status"><LoaderCircle class="size-4 animate-spin" /><span>{{ streaming ? '正在编排图表…' : '正在绘制图表…' }}</span></div>
    <div v-else-if="imageUrl" class="dsh-diagram-graphic"><img :src="imageUrl" :alt="label" /></div>
    <DshImagePreview v-if="previewOpen && imageUrl" :images="[{ url: imageUrl, label }]" :index="0" media-label="图表" downloadable @close="closePreview" @download="download" />
  </section>
</template>

<style scoped>
.dsh-diagram { min-width: 0; max-width: 100%; overflow: hidden; margin: 14px 0; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); }
.dsh-diagram-toolbar { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 8px; padding: 5px 8px 5px 12px; border-bottom: 1px solid var(--studio-border); }
.dsh-diagram-label { display: flex; min-width: 0; align-items: center; gap: 7px; color: var(--muted-foreground); font-size: 12px; line-height: 1.4; }
.dsh-diagram-label svg { flex: none; }
.dsh-diagram-tools { display: flex; flex: none; gap: 2px; }
.dsh-diagram-tools button { display: grid; width: 30px; height: 30px; flex: none; place-items: center; padding: 0; border: 0; border-radius: 4px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-diagram-tools button:hover:not(:disabled), .dsh-diagram-tools button[aria-pressed="true"] { background: var(--studio-hover); color: var(--foreground); }
.dsh-diagram-tools button:focus-visible { outline: 2px solid var(--ring); outline-offset: -2px; }
.dsh-diagram-tools button:disabled { opacity: .4; cursor: not-allowed; }
.dsh-diagram-loading { display: flex; min-height: 180px; align-items: center; justify-content: center; gap: 8px; color: var(--muted-foreground); font-size: 13px; }
.dsh-diagram-graphic { display: grid; min-height: 160px; place-items: center; padding: 18px; }
.dsh-diagram-graphic img { display: block; width: 100%; max-height: 440px; object-fit: contain; }
.dsh-diagram-source { padding: 12px; }
.dsh-diagram-source p { margin: 0 0 8px; color: var(--muted-foreground); font-size: 12px; }
.dsh-diagram-source pre { max-height: 440px; overflow: auto; margin: 0; white-space: pre; font-size: 12px; scrollbar-width: thin; }
@media (pointer: coarse) { .dsh-diagram-tools button { width: 38px; height: 44px; } .dsh-diagram-toolbar { flex-wrap: wrap; } }
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } }
</style>
