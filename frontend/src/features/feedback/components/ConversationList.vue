<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from "vue";
import { ChevronLeft, ChevronRight, MessageSquare, Search } from "@lucide/vue";
import ComposerSelect from "@/components/ComposerSelect.vue";
import { feedback, isFeedbackAdmin, loadFeedbackThreads, selectFeedbackThread } from "../composables/useFeedback";
import { categoryLabels, statusLabels } from "../types";

const pages = computed(() => Math.max(1, Math.ceil(feedback.total / 25)));
const statusOptions = computed(() => [
  { value: "", label: "全部状态" },
  ...Object.entries(statusLabels).map(([value, label]) => ({ value, label: value === "waiting_user" && isFeedbackAdmin.value ? "待用户补充" : label })),
]);
const categoryOptions = [{ value: "", label: "全部类型" }, ...Object.entries(categoryLabels).map(([value, label]) => ({ value, label }))];
let timer = 0;
watch(() => [feedback.search, feedback.status, feedback.category, feedback.unreadOnly], () => {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    if (feedback.page !== 1) feedback.page = 1;
    else void loadFeedbackThreads();
  }, 250);
});
watch(() => feedback.page, () => { void loadFeedbackThreads(); });
onBeforeUnmount(() => window.clearTimeout(timer));
const date = (value: string) => new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
</script>

<template>
  <section class="feedback-conversations" aria-label="反馈会话列表">
    <div class="feedback-list-filters">
      <label class="feedback-search"><Search :size="16" /><input v-model="feedback.search" type="search" aria-label="搜索反馈" :placeholder="isFeedbackAdmin ? '搜索问题或用户' : '搜索我的反馈'" maxlength="100" /></label>
      <div class="feedback-filter-row">
        <ComposerSelect v-model="feedback.status" label="" aria-label="按处理状态筛选" variant="field" :options="statusOptions" />
        <ComposerSelect v-model="feedback.category" label="" aria-label="按反馈类型筛选" variant="field" :options="categoryOptions" />
      </div>
      <label class="feedback-unread-filter"><input v-model="feedback.unreadOnly" type="checkbox" />只看未读<span>{{ feedback.total }} 个反馈</span></label>
    </div>
    <div class="feedback-conversation-scroll">
      <div v-if="feedback.loading && !feedback.threads.length" class="feedback-list-skeleton" aria-label="反馈加载中"><span v-for="i in 4" :key="i" /></div>
      <div v-else-if="!feedback.threads.length" class="feedback-empty"><MessageSquare :size="30" /><strong>{{ feedback.search || feedback.status || feedback.category || feedback.unreadOnly ? '没有匹配的反馈' : '暂时没有反馈' }}</strong><span>{{ isFeedbackAdmin ? '用户的新反馈会显示在这里。' : '遇到问题或有新想法，都可以告诉我们。' }}</span></div>
      <button v-for="thread in feedback.threads" :key="thread.id" type="button" class="feedback-conversation" :class="{ 'is-selected': feedback.selected?.id === thread.id }" @click="selectFeedbackThread(thread)">
        <span class="feedback-conversation-heading"><span class="feedback-avatar">{{ thread.owner_name.slice(0, 1) }}</span><strong>{{ isFeedbackAdmin ? thread.owner_name : thread.title }}</strong><time>{{ date(thread.updated_at) }}</time></span>
        <span v-if="isFeedbackAdmin" class="feedback-conversation-title">{{ thread.title }}</span>
        <span class="feedback-conversation-preview">{{ thread.preview }}</span>
        <span class="feedback-conversation-footer"><span class="feedback-status" :data-status="thread.status">{{ thread.status === 'waiting_user' && isFeedbackAdmin ? '待用户补充' : statusLabels[thread.status] }}</span><span>{{ categoryLabels[thread.category] }}</span><span v-if="thread.unread" class="feedback-count">{{ thread.unread > 99 ? '99+' : thread.unread }}</span></span>
      </button>
    </div>
    <div v-if="feedback.total > 25" class="feedback-pagination"><button type="button" class="feedback-icon" aria-label="上一页反馈" :disabled="feedback.page <= 1 || feedback.loading" @click="feedback.page--"><ChevronLeft :size="18" /></button><span>{{ feedback.page }} / {{ pages }}</span><button type="button" class="feedback-icon" aria-label="下一页反馈" :disabled="feedback.page >= pages || feedback.loading" @click="feedback.page++"><ChevronRight :size="18" /></button></div>
  </section>
</template>
