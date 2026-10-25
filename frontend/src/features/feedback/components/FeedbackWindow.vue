<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowLeft, ExternalLink, LoaderCircle, Maximize2, Minimize2, Plus, X } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";
import { webConfig } from "@/lib/config";
import { feedback, createFeedback, isFeedbackAdmin, loadFeedbackThreads, sendFeedbackMessage, updateFeedback } from "../composables/useFeedback";
import { categoryLabels, statusLabels, type FeedbackCategory, type MessageDraft } from "../types";
import ConversationList from "./ConversationList.vue";
import MessageComposer from "./MessageComposer.vue";
import MessageThread from "./MessageThread.vue";

const route = useRoute();
const router = useRouter();
const title = ref("");
const category = ref<FeedbackCategory>("bug");
const updating = ref(false);
const heading = computed(() => feedback.view === "new" ? "新的反馈" : feedback.view === "chat" ? feedback.selected?.title || "反馈详情" : isFeedbackAdmin.value ? "用户反馈" : "意见与反馈");
watch(() => feedback.open, (open) => { if (open) void loadFeedbackThreads(); });
async function submit(message: MessageDraft) {
  if (feedback.view === "new") {
    await createFeedback({ ...message, title: title.value.trim(), category: category.value, task_id: "", source_page: route.path, app_version: webConfig.appVersion });
    title.value = "";
  } else await sendFeedbackMessage(message);
}
async function reopen() {
  updating.value = true;
  try { await updateFeedback({ status: "processing" }); }
  catch (error) { feedback.error = error instanceof Error ? error.message : "重新打开失败"; }
  finally { updating.value = false; }
}
function workspace() { feedback.open = false; void router.push({ path: "/feedback", query: feedback.selected ? { thread: feedback.selected.id } : {} }); }
</script>

<template>
  <section class="feedback-window feedback-surface" :class="{ 'is-expanded': feedback.expanded }" role="dialog" aria-label="意见与反馈" @keydown.esc.stop="feedback.open = false">
    <header class="feedback-window-header">
      <button v-if="feedback.view !== 'list'" type="button" class="feedback-icon" aria-label="返回反馈列表" @click="feedback.view = 'list'"><ArrowLeft :size="19" /></button>
      <div class="feedback-window-heading"><h2>{{ heading }}</h2><span><i :class="{ 'is-online': feedback.connected }" />{{ feedback.connected ? '消息实时同步' : feedback.connecting ? '正在连接…' : '连接恢复中，仍可提交反馈' }}</span></div>
      <button v-if="isFeedbackAdmin" type="button" class="feedback-icon" aria-label="打开反馈管理" title="打开反馈管理" @click="workspace"><ExternalLink :size="17" /></button>
      <button type="button" class="feedback-icon feedback-expand" :aria-label="feedback.expanded ? '缩小反馈窗口' : '放大反馈窗口'" @click="feedback.expanded = !feedback.expanded"><Minimize2 v-if="feedback.expanded" :size="17" /><Maximize2 v-else :size="17" /></button>
      <button type="button" class="feedback-icon" aria-label="收起反馈窗口" @click="feedback.open = false"><X :size="19" /></button>
    </header>
    <div v-if="feedback.error" class="feedback-error feedback-global-error" role="alert">{{ feedback.error }}<button type="button" class="feedback-icon" aria-label="关闭提示" @click="feedback.error = ''"><X :size="14" /></button></div>
    <template v-if="feedback.view === 'list'">
      <div class="feedback-list-heading"><span>{{ isFeedbackAdmin ? '团队的问题与建议' : '我的反馈' }}</span><button type="button" class="feedback-text-button" @click="feedback.view = 'new'"><Plus :size="16" />新反馈</button></div>
      <ConversationList />
    </template>
    <template v-else-if="feedback.view === 'new'">
      <div class="feedback-new-fields"><label>反馈标题<input v-model="title" maxlength="160" placeholder="用一句话描述问题（可选）" /></label><fieldset><legend>反馈类型</legend><div class="feedback-category-options"><label v-for="(label, value) in categoryLabels" :key="value" :class="{ 'is-selected': category === value }"><input v-model="category" type="radio" :value="value" name="feedback-category" />{{ label }}</label></div></fieldset></div>
      <div class="feedback-new-space"><span class="feedback-muted">管理员回复后，这里会提醒你。</span></div>
      <MessageComposer context="new" :submit="submit" />
    </template>
    <template v-else>
      <div v-if="feedback.selected" class="feedback-thread-summary"><span class="feedback-status" :data-status="feedback.selected.status">{{ statusLabels[feedback.selected.status] }}</span><span>{{ feedback.selected.assigned_name ? `${feedback.selected.assigned_name} 正在跟进` : '管理员会尽快查看' }}</span><button v-if="feedback.selected.status === 'resolved'" type="button" class="feedback-text-button" :disabled="updating" @click="reopen"><LoaderCircle v-if="updating" :size="14" class="animate-spin" />重新打开</button></div>
      <MessageThread />
      <MessageComposer v-if="feedback.selected" :key="feedback.selected.id" :context="feedback.selected.id" :admin="isFeedbackAdmin" :submit="submit" />
    </template>
  </section>
</template>
