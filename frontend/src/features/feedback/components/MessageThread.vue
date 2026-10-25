<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ArrowDown, LoaderCircle, MessagesSquare } from "@lucide/vue";
import { sessionState } from "@/stores/session";
import { feedback, loadOlderFeedback, markFeedbackRead } from "../composables/useFeedback";
import FeedbackImage from "./FeedbackImage.vue";

const scroll = ref<HTMLElement>();
const atBottom = ref(true);
const error = ref("");
const subjectId = computed(() => sessionState.session?.subjectId);
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
function read() { if (atBottom.value) void markFeedbackRead().catch(() => {}); }
function onScroll() {
  const box = scroll.value;
  atBottom.value = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  read();
}
async function bottom() { await nextTick(); if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight; atBottom.value = true; read(); }
async function older() {
  const height = scroll.value?.scrollHeight || 0;
  const top = scroll.value?.scrollTop || 0;
  error.value = "";
  try {
    await loadOlderFeedback();
    await nextTick();
    if (scroll.value) scroll.value.scrollTop = top + scroll.value.scrollHeight - height;
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "历史消息加载失败"; }
}
watch(() => feedback.messages.at(-1)?.id, async () => { if (atBottom.value) await bottom(); });
watch(() => feedback.selected?.id, () => { atBottom.value = true; void bottom(); });
watch(() => feedback.loadingMessages, (loading) => { if (!loading) void bottom(); });
function visible() { if (!document.hidden) read(); }
onMounted(() => { void bottom(); document.addEventListener("visibilitychange", visible); });
onBeforeUnmount(() => document.removeEventListener("visibilitychange", visible));
</script>

<template>
  <div class="feedback-message-area">
    <div ref="scroll" class="feedback-message-scroll" role="log" aria-label="反馈聊天记录" aria-live="polite" @scroll="onScroll">
      <div v-if="feedback.loadingMessages" class="feedback-message-skeleton" aria-label="消息加载中"><span /><span /><span /></div>
      <template v-else>
        <button v-if="feedback.hasOlder" type="button" class="feedback-load-older" :disabled="feedback.loadingOlder" @click="older"><LoaderCircle v-if="feedback.loadingOlder" :size="14" class="animate-spin" />{{ feedback.loadingOlder ? '加载中' : '查看更早消息' }}</button>
        <p v-if="error" class="feedback-error" role="alert">{{ error }}</p>
        <div v-if="!feedback.messages.length" class="feedback-empty"><MessagesSquare :size="28" /><strong>开始沟通</strong><span>你的反馈和回复会保存在这里。</span></div>
        <template v-for="message in feedback.messages" :key="message.id">
          <div v-if="message.kind === 'status'" class="feedback-system-message">{{ message.sender_name }} {{ message.content }} · {{ time(message.created_at) }}</div>
          <article v-else class="feedback-message" :class="{ 'is-own': message.sender_id === subjectId, 'is-note': message.internal }">
            <div class="feedback-message-meta"><strong>{{ message.sender_id === subjectId ? '我' : message.sender_name }}</strong><span v-if="message.internal" class="feedback-note-tag">内部备注</span><span v-else-if="message.sender_role === 'admin'" class="feedback-staff-tag">管理员</span><time>{{ time(message.created_at) }}</time></div>
            <div class="feedback-message-bubble"><p v-if="message.content">{{ message.content }}</p><div v-if="message.attachments.length" class="feedback-message-images"><FeedbackImage v-for="item in message.attachments" :key="item.id" :attachment="item" /></div></div>
            <span v-if="message.sender_id === subjectId && !message.internal" class="feedback-delivery">{{ message.seq <= (feedback.selected?.other_read_seq || 0) ? '已读' : '已发送' }}</span>
          </article>
        </template>
      </template>
    </div>
    <button v-if="!atBottom" type="button" class="feedback-new-message" @click="bottom"><ArrowDown :size="14" />回到最新消息</button>
  </div>
</template>
