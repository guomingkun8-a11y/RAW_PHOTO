<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { ArrowUp, ImagePlus, LoaderCircle, X } from "@lucide/vue";
import { feedbackApi } from "../feedback-api";
import { sessionState } from "@/stores/session";
import { feedbackDrafts } from "../composables/useFeedback";
import type { FeedbackAttachment, MessageDraft } from "../types";

const props = defineProps<{ context: string; admin?: boolean; submit: (message: MessageDraft) => Promise<void> }>();
const draftKey = `${sessionState.session?.subjectId}:${props.context}`;
const saved = feedbackDrafts.get(draftKey);
const input = ref<HTMLInputElement>();
const textarea = ref<HTMLTextAreaElement>();
const content = ref(saved?.content || "");
const internal = ref(saved?.internal || false);
const attachments = ref<FeedbackAttachment[]>(saved?.attachments || []);
const sending = ref(false);
const uploading = ref(false);
const error = ref("");
let requestId = saved?.requestId || "";
let requestSignature = saved?.signature || "";
const canSend = computed(() => !sending.value && !uploading.value && (content.value.trim() || attachments.value.length));

function createRequestId() {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  return `client-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

async function upload(files: File[]) {
  if (uploading.value || sending.value) return;
  error.value = "";
  if (attachments.value.length + files.length > 6) { error.value = "每条消息最多 6 张截图"; return; }
  if (files.some((file) => !["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 8 * 1024 * 1024)) {
    error.value = "请上传 PNG、JPG 或 WebP 图片，每张不超过 8MB";
    return;
  }
  uploading.value = true;
  try {
    for (const file of files) attachments.value.push(await feedbackApi.upload(file));
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "截图上传失败"; }
  finally { uploading.value = false; if (input.value) input.value.value = ""; }
}

function paste(event: ClipboardEvent) {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) { event.preventDefault(); void upload(files); }
}

async function send() {
  if (!canSend.value) return;
  const body = { content: content.value.trim(), attachment_ids: attachments.value.map((item) => item.id), internal: !!props.admin && internal.value };
  const signature = JSON.stringify(body);
  if (!requestId || signature !== requestSignature) { requestId = createRequestId(); requestSignature = signature; }
  sending.value = true;
  error.value = "";
  try {
    await props.submit({ ...body, client_id: requestId });
    content.value = "";
    attachments.value = [];
    requestId = requestSignature = "";
    feedbackDrafts.delete(draftKey);
    textarea.value?.focus();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "未确认发送，请重试"; }
  finally { sending.value = false; }
}

function keydown(event: KeyboardEvent) {
  const isEnter = event.key === "Enter" || event.code === "Enter" || event.code === "NumpadEnter" || event.keyCode === 13 || event.which === 13;
  if (!isEnter || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  event.stopPropagation();
  void send();
}

defineExpose({ focus: () => textarea.value?.focus() });
onBeforeUnmount(() => {
  if (!sessionState.session || !draftKey.startsWith(`${sessionState.session.subjectId}:`)) return;
  if (!content.value && !attachments.value.length) return;
  feedbackDrafts.set(draftKey, { content: content.value, attachments: attachments.value, internal: internal.value, requestId, signature: requestSignature });
  if (feedbackDrafts.size > 50) feedbackDrafts.delete(feedbackDrafts.keys().next().value!);
});
</script>

<template>
  <form class="feedback-composer" :class="{ 'is-internal': admin && internal }" aria-label="发送反馈消息" @submit.prevent="send" @dragover.prevent @drop.prevent="upload([...($event.dataTransfer?.files || [])])">
    <div v-if="admin" class="feedback-composer-mode"><label><input v-model="internal" type="checkbox" :disabled="sending" />内部备注，仅管理员可见</label></div>
    <div class="feedback-composer-box" @click.self="textarea?.focus()">
      <div v-if="attachments.length" class="feedback-pending-files">
        <span v-for="item in attachments" :key="item.id"><span>{{ item.filename }}</span><button type="button" class="feedback-icon" :aria-label="`移除截图：${item.filename}`" :disabled="sending" @click="attachments = attachments.filter((file) => file.id !== item.id)"><X :size="14" /></button></span>
      </div>
      <textarea ref="textarea" v-model="content" :aria-label="internal ? '内部备注内容' : '消息内容'" :placeholder="internal ? '记录排查信息…' : '描述你遇到的问题或建议…'" rows="3" maxlength="8000" :disabled="sending" @paste="paste" @keydown="keydown" />
      <div v-if="error" class="feedback-error" role="alert">{{ error }}</div>
      <div class="feedback-composer-actions">
        <input ref="input" type="file" multiple accept="image/png,image/jpeg,image/webp" class="sr-only" tabindex="-1" aria-label="选择截图" @change="upload([...(input?.files || [])])" />
        <button type="button" class="feedback-icon" aria-label="上传截图" title="上传截图" :disabled="uploading || sending" @click="input?.click()"><LoaderCircle v-if="uploading" :size="19" class="animate-spin" /><ImagePlus v-else :size="19" /></button>
        <span v-if="uploading || content.length" class="feedback-muted">{{ uploading ? '截图上传中…' : `${content.length} / 8000` }}</span>
        <button type="button" class="feedback-send feedback-composer-send" :disabled="!canSend" :aria-label="sending ? '正在发送' : '发送消息'" :title="internal ? '发送内部备注' : '发送消息'" @click="send"><LoaderCircle v-if="sending" :size="19" class="animate-spin" /><ArrowUp v-else :size="20" /></button>
      </div>
    </div>
  </form>
</template>
