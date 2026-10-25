<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ArrowLeft, Info, LoaderCircle, MessageSquare, Settings2, UserRoundCheck, X } from "@lucide/vue";
import { useRoute } from "vue-router";
import { sessionState } from "@/stores/session";
import { fetchUsers, type UserAccount } from "@/lib/api";
import ComposerSelect from "@/components/ComposerSelect.vue";
import { feedbackApi } from "../feedback-api";
import { feedback, loadFeedbackThreads, selectFeedbackThread, sendFeedbackMessage, syncFeedback, updateFeedback } from "../composables/useFeedback";
import { categoryLabels, statusLabels, type FeedbackSettings } from "../types";
import ConversationList from "../components/ConversationList.vue";
import MessageComposer from "../components/MessageComposer.vue";
import MessageThread from "../components/MessageThread.vue";
import "../feedback.css";

const route = useRoute();
const details = ref(false);
const busy = ref(false);
const settingsOpen = ref(false);
const settings = ref<FeedbackSettings>();
const settingsSaving = ref(false);
const settingsError = ref("");
const administrators = ref<UserAccount[]>([]);
const superAdmin = computed(() => sessionState.session?.subjectId === "local-admin");
const statusOptions = Object.entries(statusLabels).map(([value, label]) => ({ value, label: value === "waiting_user" ? "待用户补充" : label }));
const assigneeOptions = computed(() => {
  const options = [{ value: "", label: "暂未分配" }, ...administrators.value.map((person) => ({ value: person.id, label: person.name || person.username }))];
  const selected = feedback.selected;
  if (selected?.assigned_to && !options.some((option) => option.value === selected.assigned_to)) {
    options.push({ value: selected.assigned_to, label: selected.assigned_name || "当前处理人" });
  }
  return options;
});
async function change(changes: object) {
  busy.value = true;
  try { await updateFeedback(changes); }
  catch (error) { feedback.error = error instanceof Error ? error.message : "更新失败"; }
  finally { busy.value = false; }
}
async function openSettings() {
  settingsOpen.value = !settingsOpen.value;
  if (!settingsOpen.value) return;
  settingsError.value = "";
  try { settings.value = await feedbackApi.settings(); }
  catch (error) { settingsError.value = error instanceof Error ? error.message : "配置加载失败"; }
}
async function saveSettings() {
  if (!settings.value) return;
  settingsSaving.value = true;
  settingsError.value = "";
  try { settings.value = await feedbackApi.saveSettings({ notify_enabled: settings.value.notify_enabled, site_url: settings.value.site_url }); settingsOpen.value = false; }
  catch (error) { settingsError.value = error instanceof Error ? error.message : "保存失败"; }
  finally { settingsSaving.value = false; }
}
watch(() => route.query.thread, (id) => { if (typeof id === "string") void selectFeedbackThread(id); }, { immediate: true });
onMounted(() => {
  feedback.open = false;
  feedback.active = true;
  void loadFeedbackThreads();
  void syncFeedback();
  void fetchUsers().then((result) => { administrators.value = result.items.filter((user) => user.role === "admin" && user.enabled); }).catch(() => {});
});
onBeforeUnmount(() => { feedback.active = false; });
</script>

<template>
  <div class="feedback-admin">
    <header class="feedback-admin-heading"><div><h1>反馈管理</h1><p>用户的问题、需求与建议</p></div><span class="feedback-admin-connection"><i :class="{ 'is-online': feedback.connected }" />{{ feedback.connected ? '实时接收中' : '正在恢复连接' }}</span><button v-if="superAdmin" type="button" class="feedback-secondary" @click="openSettings"><Settings2 :size="16" />通知设置</button></header>
    <form v-if="settingsOpen" class="feedback-settings" @submit.prevent="saveSettings">
      <h2>企业微信提醒</h2>
      <template v-if="settings">
        <label><input v-model="settings.notify_enabled" type="checkbox" />推送用户消息和管理员公开回复</label>
        <p class="feedback-muted">接收机器人：{{ settings.webhooks?.length ? settings.webhooks.map((item) => `${item.name}${item.enabled ? '' : '（已停用）'}`).join('、') : '尚未指定' }}</p>
        <label>系统访问地址<input v-model="settings.site_url" type="url" placeholder="https://你的系统域名" maxlength="512" /></label>
        <div class="feedback-settings-footer"><RouterLink to="/system-management?tab=alerts">选择反馈机器人</RouterLink><span>待投递事件 {{ settings.pending_events }}</span><button class="feedback-send" type="submit" :disabled="settingsSaving"><LoaderCircle v-if="settingsSaving" :size="16" class="animate-spin" />保存</button></div>
      </template>
      <span v-else-if="!settingsError">加载中…</span><p v-if="settingsError" class="feedback-error" role="alert">{{ settingsError }}</p>
    </form>
    <div v-if="feedback.error" class="feedback-error feedback-global-error" role="alert">{{ feedback.error }}<button type="button" class="feedback-icon" aria-label="关闭提示" @click="feedback.error = ''"><X :size="14" /></button></div>
    <div class="feedback-admin-workspace feedback-surface" :class="{ 'has-selection': !!feedback.selected || feedback.loadingMessages, 'show-details': details }">
      <aside class="feedback-admin-sidebar"><div class="feedback-list-heading"><strong>全部反馈</strong><span v-if="feedback.unread" class="feedback-count">{{ feedback.unread > 99 ? '99+' : feedback.unread }}</span></div><ConversationList /></aside>
      <section class="feedback-admin-chat" aria-label="反馈处理">
        <template v-if="feedback.selected || feedback.loadingMessages">
          <header class="feedback-admin-thread-header"><button type="button" class="feedback-icon feedback-mobile-back" aria-label="返回会话列表" @click="feedback.selected = null"><ArrowLeft :size="18" /></button><div><h2>{{ feedback.selected?.title || '加载中…' }}</h2><span>{{ feedback.selected?.owner_name }}</span></div><button type="button" class="feedback-icon" :aria-label="details ? '收起反馈信息' : '展开反馈信息'" @click="details = !details"><Info :size="18" /></button></header>
          <div v-if="feedback.selected" class="feedback-admin-actions">
            <ComposerSelect :model-value="feedback.selected.status" label="" aria-label="处理状态" variant="field" :options="statusOptions" :disabled="busy" @update:model-value="change({ status: $event })" />
            <button type="button" class="feedback-text-button" :disabled="busy" @click="change({ claim: feedback.selected.assigned_to !== sessionState.session?.subjectId })"><UserRoundCheck :size="15" />{{ feedback.selected.assigned_to === sessionState.session?.subjectId ? '取消认领' : '由我跟进' }}</button><span v-if="feedback.selected.assigned_name" class="feedback-muted">{{ feedback.selected.assigned_name }}</span>
          </div>
          <MessageThread /><MessageComposer v-if="feedback.selected" :key="feedback.selected.id" :context="feedback.selected.id" admin :submit="sendFeedbackMessage" />
        </template>
        <div v-else class="feedback-empty feedback-admin-empty"><MessageSquare :size="36" /><strong>选择一条反馈开始处理</strong><span>回复用户、查看截图，并跟进问题进度。</span></div>
      </section>
      <aside v-if="details && feedback.selected" class="feedback-details"><div class="feedback-list-heading"><h2>反馈信息</h2><button type="button" class="feedback-icon" aria-label="收起反馈信息" @click="details = false"><X :size="17" /></button></div><dl><dt>提交用户</dt><dd>{{ feedback.selected.owner_name }}</dd><dt>反馈类型</dt><dd>{{ categoryLabels[feedback.selected.category] }}</dd><dt>来源页面</dt><dd>{{ feedback.selected.source_page || '未记录' }}</dd><dt>关联任务</dt><dd>{{ feedback.selected.task_id || '未关联' }}</dd><dt>提交版本</dt><dd>{{ feedback.selected.app_version || '未记录' }}</dd><dt>提交时间</dt><dd>{{ new Date(feedback.selected.created_at).toLocaleString('zh-CN') }}</dd><dt>处理人</dt><dd><ComposerSelect :model-value="feedback.selected.assigned_to || ''" label="" aria-label="分配处理人" variant="field" :disabled="busy" :options="assigneeOptions" @update:model-value="change({ assigned_to: $event })" /></dd></dl></aside>
    </div>
  </div>
</template>
