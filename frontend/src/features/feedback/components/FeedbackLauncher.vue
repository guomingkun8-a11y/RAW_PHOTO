<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { MessageCircle } from "@lucide/vue";
import { useRoute } from "vue-router";
import { sessionState } from "@/stores/session";
import { feedback, startFeedback, stopFeedback, syncFeedback } from "../composables/useFeedback";
import FeedbackWindow from "./FeedbackWindow.vue";
import "../feedback.css";

const route = useRoute();
const button = ref<HTMLButtonElement>();
watch(() => sessionState.session?.key, (token) => { if (token) startFeedback(token); else stopFeedback(); }, { immediate: true });
watch(() => [feedback.open, feedback.view, route.path], () => {
  feedback.active = route.path === "/feedback" || (feedback.open && feedback.view === "chat");
  if (feedback.open) void syncFeedback();
});
watch(() => feedback.open, async (open) => {
  await nextTick();
  if (!open) button.value?.focus();
  else document.querySelector<HTMLElement>('.feedback-window input[type="search"], .feedback-window textarea')?.focus();
});
onBeforeUnmount(stopFeedback);
function toggle() {
  if (route.path === "/feedback") { document.querySelector<HTMLElement>(".feedback-admin input[type=search]")?.focus(); return; }
  feedback.open = !feedback.open;
}
</script>

<template>
  <button ref="button" type="button" class="feedback-launcher" aria-label="问题反馈" title="问题反馈" :aria-expanded="feedback.open" @click="toggle"><MessageCircle :size="18" /><span v-if="feedback.unread" class="feedback-launcher-badge">{{ feedback.unread > 99 ? '99+' : feedback.unread }}</span></button>
  <Teleport to="body"><FeedbackWindow v-if="feedback.open && route.path !== '/feedback'" /></Teleport>
</template>
