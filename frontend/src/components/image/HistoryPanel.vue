<script setup lang="ts">
import { MessageSquarePlus, Pencil, Sparkles, Trash2 } from "@lucide/vue";
import { nextTick, ref } from "vue";

import { getImageConversationStats, type ImageConversation } from "@/stores/image-conversations";

defineProps<{
  conversations: ImageConversation[];
  loading: boolean;
  selectedId: string | null;
  formatTime: (value: string) => string;
  compact?: boolean;
}>();
const emit = defineEmits<{
  create: [];
  clear: [];
  select: [id: string];
  remove: [id: string];
  rename: [id: string, title: string];
}>();

const editingId = ref<string | null>(null);
const editingTitle = ref("");
const editInput = ref<HTMLInputElement | null>(null);

async function startRename(conversation: ImageConversation) {
  editingId.value = conversation.id;
  editingTitle.value = conversation.title;
  await nextTick();
  editInput.value?.focus();
  editInput.value?.select();
}
function commitRename() {
  const title = editingTitle.value.trim();
  if (editingId.value && title) emit("rename", editingId.value, title);
  editingId.value = null;
  editingTitle.value = "";
}
</script>

<template>
  <aside class="h-full min-h-0 overflow-hidden">
    <div class="flex h-full min-h-0 flex-col gap-4">
      <div v-if="!compact" class="space-y-3"><div><h2 class="text-[22px] font-semibold text-slate-950 dark:text-stone-50">任务历史</h2><p class="mt-1 text-[13px] leading-5 text-slate-500 dark:text-stone-400">每次生成都是可复用的创作上下文。</p></div><div class="flex items-center gap-2"><button type="button" class="studio-button inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-2xl bg-slate-950 text-white" @click="emit('create')"><MessageSquarePlus class="size-4" />新建任务</button><button type="button" class="studio-button inline-flex size-11 items-center justify-center rounded-2xl border border-black/[0.06] text-slate-500 hover:bg-rose-50 hover:text-rose-600 dark:border-white/10" :disabled="!conversations.length" aria-label="清空历史" @click="emit('clear')"><Trash2 class="size-4" /></button></div></div>
      <div class="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        <div v-if="loading" class="space-y-3"><div v-for="index in 4" :key="index" class="studio-skeleton h-[86px] rounded-2xl" /></div>
        <div v-else-if="!conversations.length" class="rounded-[20px] border border-dashed border-slate-300 bg-white px-4 py-5 text-sm leading-6 text-slate-500 dark:border-white/10 dark:bg-white/[0.04]"><div class="mb-3 flex size-10 items-center justify-center rounded-2xl bg-[#4F7CFF]/10 text-[#4F7CFF]"><Sparkles class="size-5" /></div>还没有生成记录。提交第一个任务后，这里会沉淀历史、状态和可复用配置。</div>
        <article v-for="conversation in conversations" v-else :key="conversation.id" class="group relative w-full rounded-[20px] border px-4 py-3.5 text-left transition" :class="conversation.id === selectedId ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-slate-950 shadow-[0_16px_36px_rgba(79,124,255,0.12)] dark:text-white' : 'border-black/[0.06] bg-white text-slate-700 hover:border-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200'">
          <button type="button" class="block w-full pr-10 text-left" @click="emit('select', conversation.id)"><input v-if="editingId === conversation.id" ref="editInput" v-model="editingTitle" class="studio-input h-9 px-2 text-sm" @click.stop @blur="commitRename" @keydown.enter.prevent="commitRename" @keydown.esc.prevent="editingId = null" /><span v-else class="block truncate text-[15px] font-semibold">{{ conversation.title }}</span><span class="mt-1.5 block text-xs text-slate-400">{{ conversation.turns.length }} 轮 / {{ formatTime(conversation.updatedAt) }}</span><div v-if="getImageConversationStats(conversation).running || getImageConversationStats(conversation).queued" class="mt-3 flex gap-2 text-[11px] font-semibold"><span v-if="getImageConversationStats(conversation).running" class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[#315be8]">处理中 {{ getImageConversationStats(conversation).running }}</span><span v-if="getImageConversationStats(conversation).queued" class="rounded-full bg-amber-50 px-2 py-1 text-amber-700">排队 {{ getImageConversationStats(conversation).queued }}</span></div></button>
          <div class="absolute right-2 top-3 flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100"><button type="button" class="studio-button inline-flex size-8 items-center justify-center rounded-xl text-slate-400 hover:bg-[#4F7CFF]/10 hover:text-[#315be8]" aria-label="重命名会话" @click.stop="startRename(conversation)"><Pencil class="size-3.5" /></button><button type="button" class="studio-button inline-flex size-8 items-center justify-center rounded-xl text-slate-400 hover:bg-rose-50 hover:text-rose-600" aria-label="删除会话" @click.stop="emit('remove', conversation.id)"><Trash2 class="size-3.5" /></button></div>
        </article>
      </div>
    </div>
  </aside>
</template>
