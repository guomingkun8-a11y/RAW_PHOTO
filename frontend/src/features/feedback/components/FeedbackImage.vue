<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { Download, ImageOff, LoaderCircle, X } from "@lucide/vue";
import { feedbackApi } from "../feedback-api";
import type { FeedbackAttachment } from "../types";

const props = defineProps<{ attachment: FeedbackAttachment }>();
const url = ref("");
const error = ref(false);
const dialog = ref<HTMLDialogElement>();
let generation = 0;
async function load() {
  const version = ++generation;
  error.value = false;
  try {
    const result = await feedbackApi.image(props.attachment.id);
    if (version !== generation) { URL.revokeObjectURL(result); return; }
    if (url.value) URL.revokeObjectURL(url.value);
    url.value = result;
  } catch { if (version === generation) error.value = true; }
}
watch(() => props.attachment.id, load, { immediate: true });
onBeforeUnmount(() => { generation++; if (url.value) URL.revokeObjectURL(url.value); });
</script>

<template>
  <button v-if="url" type="button" class="feedback-image" :aria-label="`查看截图：${attachment.filename}`" @click="dialog?.showModal()">
    <img :src="url" :alt="attachment.filename" loading="lazy" />
  </button>
  <button v-else-if="error" type="button" class="feedback-image feedback-image-placeholder" aria-label="重新加载截图" @click="load"><ImageOff :size="20" /><span>重新加载</span></button>
  <div v-else class="feedback-image feedback-image-placeholder" aria-label="截图加载中"><LoaderCircle :size="20" class="animate-spin" /></div>
  <dialog ref="dialog" class="feedback-image-dialog" @click="($event.target === dialog) && dialog?.close()">
    <div class="feedback-image-toolbar"><span>{{ attachment.filename }}</span><a :href="url" :download="attachment.filename" class="feedback-icon" aria-label="下载截图" title="下载截图"><Download :size="18" /></a><button type="button" class="feedback-icon" aria-label="关闭截图" @click="dialog?.close()"><X :size="20" /></button></div>
    <img :src="url" :alt="attachment.filename" />
  </dialog>
</template>
