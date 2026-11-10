<script setup lang="ts">
import { computed } from "vue";
import { MessageCirclePlus, PanelLeftClose, Trash2 } from "@lucide/vue";
import type { DshAgentConversation } from "./api";

const props = defineProps<{ conversations: DshAgentConversation[]; activeId: string; busy: boolean; loading: boolean; available: boolean; admin: boolean }>();
defineEmits<{ create: []; select: [id: string]; remove: [conversation: DshAgentConversation]; collapse: [] }>();
const groups = computed(() => {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const groups = [{ label: "今天", items: [] as DshAgentConversation[] }, { label: "最近 7 天", items: [] as DshAgentConversation[] }, { label: "更早", items: [] as DshAgentConversation[] }];
  for (const conversation of props.conversations) {
    const elapsed = today.getTime() - new Date(conversation.updated_at.replace(" ", "T")).getTime();
    groups[elapsed < 0 ? 0 : elapsed < 6 * 86400000 ? 1 : 2]!.items.push(conversation);
  }
  return groups.filter(group => group.items.length);
});
function relativeTime(date: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(date.replace(" ", "T")).getTime()) / 60000));
  return minutes < 1 ? "刚刚" : minutes < 60 ? `${minutes} 分钟` : minutes < 1440 ? `${Math.floor(minutes / 60)} 小时` : `${Math.floor(minutes / 1440)} 天`;
}
</script>

<template>
  <aside id="dsh-thread-sidebar" class="dsh-sidebar" aria-label="对话历史">
    <div class="dsh-sidebar-head">
      <img class="dsh-brand-icon" src="/jiakemei-mark.svg" alt="" width="30" height="30" />
      <div><h1>AI 智能体</h1><p>DeepSeek Harness</p></div>
      <button class="dsh-sidebar-collapse" type="button" aria-label="收起对话侧边栏" title="收起对话侧边栏" aria-controls="dsh-thread-sidebar" aria-expanded="true" @click="$emit('collapse')"><PanelLeftClose class="size-4" /></button>
    </div>
    <button class="dsh-new-conversation" type="button" :disabled="busy" @click="$emit('create')"><MessageCirclePlus class="size-4" />新会话</button>
    <nav class="dsh-history-list" aria-label="最近对话">
      <h2 v-if="!groups.length">最近对话</h2>
      <section v-for="group in groups" :key="group.label">
        <h2>{{ group.label }}</h2>
        <div v-for="item in group.items" :key="item.conversation_id" class="dsh-history-item" :class="{ active: item.conversation_id === activeId }">
          <button class="dsh-history-select" type="button" :aria-current="item.conversation_id === activeId ? 'page' : undefined" :title="item.title || '新对话'" @click="$emit('select', item.conversation_id)"><strong>{{ item.title || '新对话' }}</strong><span v-if="['running', 'stopping'].includes(item.status)" class="history-running" aria-label="正在回复" /><time>{{ relativeTime(item.updated_at) }}</time></button>
          <button class="dsh-history-delete" type="button" aria-label="删除对话" title="删除对话" :disabled="busy" @click="$emit('remove', item)"><Trash2 class="size-3.5" /></button>
        </div>
      </section>
      <div v-if="!loading && !conversations.length" class="dsh-history-empty">暂无历史对话</div>
    </nav>
    <RouterLink v-if="admin" to="/ai-agent/monitoring" class="dsh-admin-link">查看用户使用与 Token 统计</RouterLink>
    <footer><span class="dsh-status-dot" :class="{ online: available }" />{{ available ? '服务可用' : '服务未就绪' }}</footer>
  </aside>
</template>

<style scoped>
.dsh-sidebar { display: flex; box-sizing: border-box; width: 260px; min-width: 0; min-height: 0; flex-direction: column; border-right: 1px solid var(--studio-border); background: var(--studio-panel); padding: 20px 12px 12px; }
.dsh-sidebar-head { display: flex; align-items: center; gap: 10px; padding: 0 8px 22px; }
.dsh-sidebar-head > img { flex: none; }
.dsh-sidebar-head > div { flex: 1; min-width: 0; }
.dsh-sidebar-head h1 { margin: 0; font-size: 15px; font-weight: 600; }
.dsh-sidebar-head p { margin: 3px 0 0; color: var(--muted-foreground); font-size: 11px; }
.dsh-sidebar-collapse { display: grid; width: 32px; height: 32px; flex: none; place-items: center; border: 0; border-radius: 6px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-sidebar-collapse:hover { background: var(--studio-hover); color: var(--foreground); }
.dsh-new-conversation { display: flex; width: 100%; min-height: 40px; align-items: center; justify-content: center; gap: 8px; margin-bottom: 20px; border: 1px solid var(--input); border-radius: 7px; background: transparent; color: var(--foreground); font-size: 13px; cursor: pointer; }
.dsh-new-conversation:hover:not(:disabled) { background: var(--studio-hover); }
.dsh-history-list { min-height: 0; flex: 1; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--input) transparent; }
.dsh-history-list section + section { margin-top: 20px; }
.dsh-history-list h2 { padding: 0 10px; margin: 0 0 7px; color: var(--muted-foreground); font-size: 11px; font-weight: 500; }
.dsh-history-item { display: flex; align-items: center; border-radius: 6px; }
.dsh-history-item:hover, .dsh-history-item.active { background: var(--secondary); }
.dsh-history-select { display: flex; min-width: 0; flex: 1; min-height: 38px; align-items: center; gap: 6px; border: 0; background: transparent; padding: 8px 6px 8px 10px; color: var(--foreground); text-align: left; cursor: pointer; }
.dsh-history-select strong { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 400; }
.dsh-history-select time { flex: none; color: var(--muted-foreground); font-size: 10px; }
.history-running { width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--accent); }
.dsh-history-delete { display: grid; width: 28px; height: 28px; flex: none; margin-right: 4px; place-items: center; border: 0; border-radius: 5px; visibility: hidden; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-history-item:hover .dsh-history-delete, .dsh-history-item:focus-within .dsh-history-delete { visibility: visible; }
.dsh-history-delete:hover { color: var(--destructive); }
.dsh-history-empty { padding: 20px 10px; color: var(--muted-foreground); font-size: 12px; }
.dsh-admin-link { margin: 12px 8px; color: var(--muted-foreground); font-size: 11px; text-decoration: none; }
.dsh-admin-link:hover { color: var(--foreground); }
footer { display: flex; align-items: center; gap: 7px; padding: 13px 8px 3px; border-top: 1px solid var(--studio-border); color: var(--muted-foreground); font-size: 11px; }
.dsh-status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--destructive); }
.dsh-status-dot.online { background: var(--ai-success); }
button:focus-visible, a:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
button:disabled { opacity: .45; cursor: not-allowed; }
@media (max-width: 760px) { .dsh-sidebar { position: fixed; z-index: 41; top: var(--studio-nav-height); bottom: 0; left: 0; width: min(86vw, 290px); } }
@media (pointer: coarse) { .dsh-sidebar-collapse { width: 44px; height: 44px; } .dsh-history-delete { visibility: visible; width: 44px; height: 44px; } .dsh-history-select { min-height: 44px; } .dsh-new-conversation { min-height: 44px; } }
</style>
