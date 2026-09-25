<script setup lang="ts">
import { ArrowDown, ChevronLeft, ChevronRight, ListChecks, LoaderCircle, MessageSquarePlus, Pencil, Search, Sparkles, Trash2, X } from "@lucide/vue";
import { computed, nextTick, ref, watch } from "vue";

import { extractUserDisplayPrompt } from "@/lib/prompt-display";
import { getImageConversationStats, type ImageConversation } from "@/stores/image-conversations";

const props = defineProps<{
  conversations: ImageConversation[];
  loading: boolean;
  selectedId: string | null;
  formatTime: (value: string) => string;
  compact?: boolean;
  hasMore?: boolean;
  loadingMore?: boolean;
  total?: number;
}>();
const emit = defineEmits<{
  create: [];
  clear: [];
  select: [id: string];
  remove: [id: string];
  deleteMany: [ids: string[]];
  rename: [id: string, title: string];
  loadMore: [];
}>();

const editingId = ref<string | null>(null);
const editingTitle = ref("");
const editInput = ref<HTMLInputElement | null>(null);
const searchQuery = ref("");
const currentPage = ref(1);
const selectionMode = ref(false);
const selectedIds = ref<Set<string>>(new Set());
const HISTORY_PAGE_SIZE = 20;
const filteredConversations = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase("zh-CN");
  if (!query) return props.conversations;
  return props.conversations.filter((conversation) => {
    const prompts = conversation.turns.map((turn) => turnDisplayPrompt(turn)).join(" ");
    return `${conversation.title} ${prompts}`.toLocaleLowerCase("zh-CN").includes(query);
  });
});
const filteredConversationIds = computed(() => filteredConversations.value.map((conversation) => conversation.id));
const totalPages = computed(() => Math.max(1, Math.ceil(filteredConversations.value.length / HISTORY_PAGE_SIZE)));
const pageStart = computed(() => filteredConversations.value.length ? (currentPage.value - 1) * HISTORY_PAGE_SIZE + 1 : 0);
const pageEnd = computed(() => Math.min(currentPage.value * HISTORY_PAGE_SIZE, filteredConversations.value.length));
const paginatedConversations = computed(() =>
  filteredConversations.value.slice(
    (currentPage.value - 1) * HISTORY_PAGE_SIZE,
    currentPage.value * HISTORY_PAGE_SIZE,
  ),
);
const selectedCount = computed(() => selectedIds.value.size);
const allFilteredSelected = computed(() => (
  filteredConversationIds.value.length > 0
  && filteredConversationIds.value.every((id) => selectedIds.value.has(id))
));

function turnDisplayPrompt(turn: ImageConversation["turns"][number]) {
  return turn.sourcePrompt || extractUserDisplayPrompt(turn.prompt) || turn.prompt;
}
async function startRename(conversation: ImageConversation) {
  selectionMode.value = false;
  selectedIds.value = new Set();
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
function matchingSnippet(conversation: ImageConversation) {
  const query = searchQuery.value.trim().toLocaleLowerCase("zh-CN");
  if (!query || conversation.title.toLocaleLowerCase("zh-CN").includes(query)) return "";
  const match = conversation.turns
    .map((turn) => turnDisplayPrompt(turn))
    .find((prompt) => prompt.toLocaleLowerCase("zh-CN").includes(query));
  if (!match) return "";
  const compact = match.replace(/\s+/g, " ").trim();
  return compact.length > 34 ? `${compact.slice(0, 34)}...` : compact;
}
function startSelectionMode() {
  editingId.value = null;
  editingTitle.value = "";
  selectionMode.value = true;
}
function cancelSelectionMode() {
  selectionMode.value = false;
  selectedIds.value = new Set();
}
function isSelected(id: string) {
  return selectedIds.value.has(id);
}
function toggleConversationSelection(id: string) {
  const next = new Set(selectedIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedIds.value = next;
}
function toggleFilteredSelection() {
  const next = new Set(selectedIds.value);
  if (allFilteredSelected.value) {
    filteredConversationIds.value.forEach((id) => next.delete(id));
  } else {
    filteredConversationIds.value.forEach((id) => next.add(id));
  }
  selectedIds.value = next;
}
function handleConversationClick(id: string) {
  if (selectionMode.value) {
    toggleConversationSelection(id);
    return;
  }
  emit("select", id);
}
function requestDeleteSelected() {
  const ids = Array.from(selectedIds.value);
  if (!ids.length) return;
  emit("deleteMany", ids);
  cancelSelectionMode();
}
function goToPage(page: number) {
  currentPage.value = Math.min(totalPages.value, Math.max(1, page));
}
watch(searchQuery, () => {
  currentPage.value = 1;
});
watch(totalPages, (pages) => {
  if (currentPage.value > pages) currentPage.value = pages;
});
watch(() => props.conversations.map((conversation) => conversation.id).join("|"), () => {
  const existingIds = new Set(props.conversations.map((conversation) => conversation.id));
  selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => existingIds.has(id)));
  if (!selectedIds.value.size && !props.conversations.length) selectionMode.value = false;
});
</script>

<template>
  <aside class="h-full min-h-0 overflow-hidden">
    <div class="flex h-full min-h-0 flex-col gap-4">
      <div v-if="!compact" class="space-y-3">
        <div>
          <h2 class="text-[22px] font-semibold text-slate-950 dark:text-stone-50">任务历史</h2>
          <p class="mt-1 text-[13px] leading-5 text-slate-500 dark:text-stone-400">每次生成都是可复用的创作上下文。</p>
        </div>
        <div class="flex items-center gap-2">
          <button type="button" class="studio-button inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-2xl bg-slate-950 text-white" @click="emit('create')">
            <MessageSquarePlus class="size-4" />新建任务
          </button>
          <button type="button" class="studio-button inline-flex size-11 items-center justify-center rounded-2xl border border-black/[0.06] text-slate-500 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10" :disabled="!conversations.length" aria-label="清空历史" @click="emit('clear')">
            <Trash2 class="size-4" />
          </button>
        </div>
      </div>
      <div v-if="conversations.length" class="flex min-h-10 items-center justify-between gap-2">
        <label v-if="selectionMode" class="inline-flex min-w-0 cursor-pointer items-center gap-2 rounded-xl px-2.5 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-stone-300 dark:hover:bg-white/[0.08]">
          <input type="checkbox" class="size-4 rounded border-slate-300 accent-[#4F7CFF]" :checked="allFilteredSelected" aria-label="选择当前列表" @change="toggleFilteredSelection" />
          <span class="truncate">{{ selectedCount ? `已选 ${selectedCount} 条` : '选择当前列表' }}</span>
        </label>
        <span v-else class="text-sm font-medium text-slate-500 dark:text-stone-400">{{ conversations.length }} 条历史</span>
        <div class="flex shrink-0 items-center gap-2">
          <button v-if="selectionMode" type="button" class="studio-button inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-rose-600 px-3 text-sm font-semibold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-45" :disabled="!selectedCount" data-testid="history-delete-selected" @click="requestDeleteSelected">
            <Trash2 class="size-4" />删除
          </button>
          <button type="button" class="studio-button inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]" data-testid="history-select-toggle" @click="selectionMode ? cancelSelectionMode() : startSelectionMode()">
            <X v-if="selectionMode" class="size-4" />
            <ListChecks v-else class="size-4" />
            {{ selectionMode ? '取消' : '选择' }}
          </button>
        </div>
      </div>
      <label v-if="conversations.length" class="relative block">
        <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" aria-hidden="true" />
        <input v-model="searchQuery" type="text" role="searchbox" class="studio-input h-11 w-full px-10 text-sm" placeholder="搜索历史对话" aria-label="搜索历史对话" />
        <button v-if="searchQuery" type="button" class="studio-button absolute right-1 top-1/2 inline-grid size-9 -translate-y-1/2 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-white/[0.08] dark:hover:text-stone-100" aria-label="清空搜索" @click="searchQuery = ''">
          <X class="size-4" />
        </button>
      </label>
      <div class="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        <div v-if="loading" class="space-y-3">
          <div class="rounded-xl bg-[#F8FAFC] px-3 py-2 text-xs text-slate-500 dark:bg-white/[0.04] dark:text-stone-400">正在读取历史记录...</div>
          <div v-for="index in 5" :key="index" class="studio-skeleton h-[86px] rounded-2xl" />
        </div>
        <div v-else-if="!conversations.length" class="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-5 text-sm leading-6 text-slate-500 dark:border-white/10 dark:bg-white/[0.04]"><div class="mb-3 flex size-10 items-center justify-center rounded-xl bg-[#4F7CFF]/10 text-[#4F7CFF]"><Sparkles class="size-5" /></div>还没有生成记录。提交第一个任务后，这里会沉淀历史、状态和可复用配置。</div>
        <div v-else-if="!filteredConversations.length" class="px-4 py-8 text-center text-sm text-slate-500 dark:text-stone-400">没有找到匹配的历史对话</div>
        <article
          v-for="(conversation, index) in paginatedConversations"
          v-else
          :key="conversation.id"
          class="history-panel-item group relative w-full rounded-xl border py-3.5 text-left transition"
          :class="[
            selectionMode ? 'px-4 pl-11' : 'px-4',
            isSelected(conversation.id) || conversation.id === selectedId
              ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-slate-950 dark:text-white'
              : 'border-black/[0.06] bg-white text-slate-700 hover:border-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200',
          ]"
          :style="{ '--history-row-index': index }"
        >
          <input
            v-if="selectionMode"
            type="checkbox"
            class="absolute left-4 top-5 size-4 rounded border-slate-300 accent-[#4F7CFF]"
            :checked="isSelected(conversation.id)"
            :aria-label="`选择 ${conversation.title}`"
            data-testid="history-select-item"
            @click.stop="toggleConversationSelection(conversation.id)"
          />
          <button type="button" class="block min-h-11 w-full pr-20 text-left" @click="handleConversationClick(conversation.id)">
            <input v-if="editingId === conversation.id" ref="editInput" v-model="editingTitle" class="studio-input h-9 px-2 text-sm" @click.stop @blur="commitRename" @keydown.enter.prevent="commitRename" @keydown.esc.prevent="editingId = null" />
            <span v-else class="block truncate text-[15px] font-semibold">{{ conversation.title }}</span>
            <span v-if="matchingSnippet(conversation)" class="mt-1 block truncate text-xs text-slate-500 dark:text-stone-300">{{ matchingSnippet(conversation) }}</span>
            <span class="mt-1.5 block text-xs text-slate-400">{{ conversation.turns.length }} 轮 / {{ formatTime(conversation.updatedAt) }}</span>
            <div v-if="getImageConversationStats(conversation).running || getImageConversationStats(conversation).queued" class="mt-3 flex gap-2 text-[11px] font-semibold">
              <span v-if="getImageConversationStats(conversation).running" class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[#315be8]">处理中 {{ getImageConversationStats(conversation).running }}</span>
              <span v-if="getImageConversationStats(conversation).queued" class="rounded-full bg-amber-50 px-2 py-1 text-amber-700">排队 {{ getImageConversationStats(conversation).queued }}</span>
            </div>
          </button>
          <div v-if="!selectionMode" class="absolute right-1 top-1 flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
            <button type="button" class="studio-button inline-flex size-11 items-center justify-center rounded-lg text-slate-400 hover:bg-[#4F7CFF]/10 hover:text-[#315be8]" aria-label="重命名会话" @click.stop="startRename(conversation)">
              <Pencil class="size-3.5" />
            </button>
            <button type="button" class="studio-button inline-flex size-11 items-center justify-center rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-600" aria-label="删除会话" @click.stop="emit('remove', conversation.id)">
              <Trash2 class="size-3.5" />
            </button>
          </div>
        </article>
      </div>
      <div v-if="!loading && filteredConversations.length" class="flex shrink-0 flex-col gap-3 border-t border-black/[0.06] pt-3 text-xs text-slate-500 dark:border-white/10 dark:text-stone-400 sm:flex-row sm:items-center sm:justify-between">
        <span>
          显示 {{ pageStart }}-{{ pageEnd }} / {{ total || filteredConversations.length }} 条，每页 20 条
        </span>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="studio-button inline-flex size-9 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
            :disabled="currentPage <= 1"
            aria-label="上一页"
            @click="goToPage(currentPage - 1)"
          >
            <ChevronLeft class="size-4" />
          </button>
          <span class="min-w-16 text-center font-semibold tabular-nums text-slate-900 dark:text-stone-100">{{ currentPage }} / {{ totalPages }}</span>
          <button
            type="button"
            class="studio-button inline-flex size-9 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
            :disabled="currentPage >= totalPages"
            aria-label="下一页"
            @click="goToPage(currentPage + 1)"
          >
            <ChevronRight class="size-4" />
          </button>
        </div>
      </div>
      <button
        v-if="!loading && hasMore && !searchQuery.trim()"
        type="button"
        class="studio-button flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl border border-black/[0.06] text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-50 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]"
        :disabled="loadingMore"
        data-testid="history-load-more"
        @click="emit('loadMore')"
      >
        <LoaderCircle v-if="loadingMore" class="size-4 animate-spin" />
        <ArrowDown v-else class="size-4" />
        {{ loadingMore ? '正在加载' : '加载更早的历史记录' }}
      </button>
    </div>
  </aside>
</template>

<style scoped>
.history-panel-item {
  animation: history-panel-row-in 180ms var(--studio-ease) both;
  animation-delay: calc(var(--history-row-index, 0) * 12ms);
}

@keyframes history-panel-row-in {
  from {
    opacity: 0.96;
    transform: translateY(4px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .history-panel-item {
    animation: none;
  }
}
</style>
