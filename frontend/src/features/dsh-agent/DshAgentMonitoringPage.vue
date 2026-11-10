<script setup lang="ts">
import { Activity, LoaderCircle, MessageSquareText, RefreshCw } from "@lucide/vue";
import { onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import DshMessageContent from "@/features/dsh-agent/DshMessageContent.vue";
import DshModelSwitch from "@/features/dsh-agent/DshModelSwitch.vue";
import DshImageResults from "./DshImageResults.vue";

import {
  dshAgentModelLabel,
  fetchDshAgentAdminConversation,
  fetchDshAgentAdminUsage,
  type DshAgentMessage,
} from "@/features/dsh-agent/api";

const summary = ref({ assistant_turns: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_available: false });
const items = ref<DshAgentMessage[]>([]);
const selected = ref<DshAgentMessage | null>(null);
const conversationMessages = ref<DshAgentMessage[]>([]);
const loading = ref(false);
const conversationLoading = ref(false);

async function loadUsage() {
  loading.value = true;
  try {
    const result = await fetchDshAgentAdminUsage(200);
    summary.value = result.summary;
    items.value = result.items;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "加载监控数据失败");
  } finally {
    loading.value = false;
  }
}

async function openConversation(item: DshAgentMessage) {
  selected.value = item;
  conversationLoading.value = true;
  try {
    conversationMessages.value = (await fetchDshAgentAdminConversation(item.conversation_id)).items;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "加载对话失败");
  } finally {
    conversationLoading.value = false;
  }
}

function formatTime(value: string) {
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN");
}

onMounted(loadUsage);
</script>

<template>
  <main class="dsh-monitor-page">
    <header class="dsh-monitor-header">
      <div><span class="dsh-monitor-icon"><Activity class="size-4" /></span><div><h1>智能体监控</h1><p>用户会话与模型 Token 用量</p></div></div>
      <button type="button" class="dsh-refresh" :disabled="loading" @click="loadUsage"><LoaderCircle v-if="loading" class="size-4 animate-spin" /><RefreshCw v-else class="size-4" />刷新</button>
    </header>
    <section class="dsh-stats">
      <article><span>助手回复</span><strong>{{ summary.assistant_turns.toLocaleString() }}</strong></article>
      <article><span>输入 Token</span><strong>{{ summary.input_tokens.toLocaleString() }}</strong></article>
      <article><span>输出 Token</span><strong>{{ summary.output_tokens.toLocaleString() }}</strong></article>
      <article><span>总 Token</span><strong>{{ summary.total_tokens.toLocaleString() }}</strong></article>
    </section>
    <div class="dsh-monitor-layout">
      <section class="dsh-usage-table">
        <div class="dsh-table-head"><strong>最近回复</strong><span>{{ items.length }} 条</span></div>
        <div v-if="loading && !items.length" class="dsh-table-empty"><LoaderCircle class="size-4 animate-spin" />正在加载</div>
        <div v-else-if="!items.length" class="dsh-table-empty">暂无智能体使用记录</div>
        <button v-for="item in items" :key="item.message_id" type="button" class="dsh-usage-row" :class="{ selected: selected?.message_id === item.message_id }" @click="openConversation(item)">
          <span class="dsh-usage-user"><strong>{{ item.owner_name || item.owner_username || item.owner_id }}</strong><small>{{ item.owner_username || item.owner_id }}</small></span>
          <span class="dsh-usage-detail"><strong>{{ item.conversation_title || '新对话' }}</strong><small>{{ dshAgentModelLabel(item.model) }} · {{ (item.content || '').slice(0, 90) }}</small></span>
          <span class="dsh-usage-count"><strong>{{ (item.total_tokens || 0).toLocaleString() }}</strong><small>tokens</small></span>
          <time>{{ formatTime(item.created_at) }}</time>
        </button>
        <p class="dsh-cost-note">DSH SDK 返回 Token usage，不包含人民币费用；费用暂不估算。</p>
      </section>
      <aside class="dsh-conversation-view">
        <div class="dsh-table-head"><strong><MessageSquareText class="size-4" />对话详情</strong><span>{{ selected?.owner_name || selected?.owner_username || '' }}</span></div>
        <div v-if="!selected" class="dsh-table-empty">选择一条回复查看完整对话</div>
        <div v-else-if="conversationLoading" class="dsh-table-empty"><LoaderCircle class="size-4 animate-spin" />正在加载</div>
        <div v-else class="dsh-admin-messages">
          <template v-for="message in conversationMessages" :key="message.message_id">
            <DshModelSwitch v-if="message.role === 'model_switch' && message.model_switch" :from-model="message.model_switch.from_model" :to-model="message.model_switch.to_model" />
            <article v-else :class="message.role">
              <strong>{{ message.role === 'user' ? (selected.owner_name || selected.owner_username || '用户') : dshAgentModelLabel(message.model) }}</strong>
              <DshMessageContent v-if="message.role === 'assistant'" class="dsh-admin-message-text" :content="message.content" />
              <p v-else class="dsh-admin-message-text">{{ message.content }}</p>
              <small>{{ formatTime(message.created_at) }}<template v-if="message.total_tokens"> · {{ message.total_tokens.toLocaleString() }} tokens</template></small>
            </article>
            <DshImageResults v-if="message.media?.length" :media="message.media" readonly />
          </template>
        </div>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.dsh-monitor-page { min-height: calc(100dvh - var(--studio-nav-height)); padding: 24px clamp(16px, 3vw, 42px) 40px; color: var(--foreground); }
.dsh-monitor-header { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 22px; }
.dsh-monitor-header > div { display: flex; align-items: center; gap: 11px; }
.dsh-monitor-icon { display: grid; width: 38px; height: 38px; place-items: center; border-radius: 9px; background: color-mix(in srgb, var(--ai-success) 10%, var(--studio-panel)); color: color-mix(in srgb, var(--ai-success) 65%, var(--foreground)); }
.dsh-monitor-header h1 { margin: 0; color: var(--foreground); font-size: 18px; font-weight: 700; }
.dsh-monitor-header p { margin: 3px 0 0; color: var(--muted-foreground); font-size: 12px; }
.dsh-refresh { display: inline-flex; min-height: 36px; align-items: center; gap: 7px; border: 1px solid var(--input); border-radius: 8px; background: var(--studio-panel); padding: 0 11px; color: var(--muted-foreground); font-size: 12px; }
.dsh-refresh:disabled { opacity: .6; }
.dsh-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.dsh-stats article { display: grid; gap: 9px; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); padding: 15px 17px; }
.dsh-stats span { color: var(--muted-foreground); font-size: 11px; }
.dsh-stats strong { color: var(--foreground); font-size: 20px; font-variant-numeric: tabular-nums; }
.dsh-monitor-layout { display: grid; min-height: 490px; grid-template-columns: minmax(0, 1.25fr) minmax(280px, .75fr); gap: 14px; }
.dsh-usage-table,.dsh-conversation-view { min-width: 0; overflow: hidden; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); }
.dsh-table-head { display: flex; min-height: 45px; align-items: center; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--studio-border); padding: 0 13px; }
.dsh-table-head strong { display: flex; align-items: center; gap: 7px; color: var(--foreground); font-size: 12px; }
.dsh-table-head span { color: var(--muted-foreground); font-size: 10px; }
.dsh-usage-row { display: grid; width: 100%; grid-template-columns: 130px minmax(0, 1fr) 70px 115px; align-items: center; gap: 12px; border: 0; border-bottom: 1px solid var(--studio-border); background: transparent; padding: 11px 13px; text-align: left; }
.dsh-usage-row:hover,.dsh-usage-row.selected { background: var(--studio-bg); }
.dsh-usage-user,.dsh-usage-detail,.dsh-usage-count { display: grid; min-width: 0; gap: 4px; }
.dsh-usage-row strong { overflow: hidden; color: var(--foreground); font-size: 11px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.dsh-usage-row small,.dsh-usage-row time { overflow: hidden; color: var(--muted-foreground); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.dsh-usage-count { text-align: right; }
.dsh-usage-row time { text-align: right; }
.dsh-cost-note { margin: 0; border-top: 1px solid var(--studio-border); padding: 10px 13px; color: var(--muted-foreground); font-size: 10px; }
.dsh-table-empty { display: flex; min-height: 170px; align-items: center; justify-content: center; gap: 8px; color: var(--muted-foreground); font-size: 12px; }
.dsh-admin-messages { display: grid; max-height: 620px; gap: 13px; overflow: auto; padding: 13px; }
.dsh-admin-messages article { border-radius: 7px; background: var(--studio-bg); padding: 10px; }
.dsh-admin-messages article.assistant { background: color-mix(in srgb, var(--ai-success) 8%, var(--studio-panel)); }
.dsh-admin-messages strong { color: var(--muted-foreground); font-size: 10px; }
.dsh-admin-message-text { margin: 6px 0; color: var(--foreground); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.dsh-admin-messages .user .dsh-admin-message-text { white-space: pre-wrap; }
.dsh-admin-messages small { color: var(--muted-foreground); font-size: 9px; }
@media (max-width: 900px) {
  .dsh-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dsh-monitor-layout { grid-template-columns: minmax(0, 1fr); }
  .dsh-conversation-view { min-height: 300px; }
}
@media (max-width: 600px) {
  .dsh-monitor-page { padding: 18px 12px 28px; }
  .dsh-usage-row { grid-template-columns: minmax(0, 1fr) 65px; gap: 6px 10px; }
  .dsh-usage-user { grid-column: 1; }
  .dsh-usage-count { grid-column: 2; grid-row: 1; }
  .dsh-usage-detail { grid-column: 1; }
  .dsh-usage-row time { grid-column: 2; grid-row: 2; }
}
</style>
