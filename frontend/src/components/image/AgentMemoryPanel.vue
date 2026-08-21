<script setup lang="ts">
import { Brain, Check, LoaderCircle, Pencil, Plus, Search, Trash2, X } from "@lucide/vue";
import { computed, ref, watch } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import {
  clearAgentMemories,
  createAgentMemory,
  deleteAgentMemory,
  fetchAgentMemories,
  updateAgentMemory,
  type AgentMemoryItem,
  type AgentMemoryScope,
} from "@/lib/api";

const props = withDefaults(defineProps<{
  open: boolean;
  enabled: boolean;
  conversationId?: string | null;
  draft?: string;
}>(), { conversationId: null, draft: "" });

const emit = defineEmits<{
  close: [];
  "update:enabled": [value: boolean];
  countChange: [value: number];
}>();

type EditorState = {
  id: string | null;
  content: string;
  category: string;
  scope: AgentMemoryScope;
  scopeId: string;
};

const items = ref<AgentMemoryItem[]>([]);
const loading = ref(false);
const saving = ref(false);
const search = ref("");
const scopeFilter = ref<"all" | AgentMemoryScope>("all");
const editor = ref<EditorState | null>(null);
const pendingDeleteId = ref<string | null>(null);
const clearPending = ref(false);

const categoryOptions = [
  { value: "identity", label: "个人信息" },
  { value: "preference", label: "个人偏好" },
  { value: "visual_style", label: "视觉风格" },
  { value: "brand_rule", label: "品牌规则" },
  { value: "product", label: "商品信息" },
  { value: "constraint", label: "限制条件" },
  { value: "decision", label: "已确认方案" },
  { value: "note", label: "其他" },
];

const scopeOptions = computed(() => {
  const options: Array<{ value: AgentMemoryScope; label: string; disabled?: boolean }> = [
    { value: "user", label: "个人偏好" },
    { value: "project", label: "当前项目", disabled: !props.conversationId },
    { value: "conversation", label: "当前会话", disabled: !props.conversationId },
  ];
  if (editor.value?.scope === "brand") options.push({ value: "brand", label: "品牌规则" });
  return options;
});

const scopeFilters = computed(() => {
  const options: Array<{ value: "all" | AgentMemoryScope; label: string }> = [
    { value: "all", label: "全部" },
    { value: "user", label: "个人" },
    { value: "project", label: "项目" },
    { value: "conversation", label: "会话" },
  ];
  if (items.value.some((item) => item.scope === "brand")) options.splice(2, 0, { value: "brand", label: "品牌" });
  return options;
});

const filteredItems = computed(() => {
  const query = search.value.trim().toLowerCase();
  return items.value.filter((item) => {
    if (scopeFilter.value !== "all" && item.scope !== scopeFilter.value) return false;
    if (!query) return true;
    return [item.content, categoryLabel(item.category), scopeLabel(item.scope)]
      .some((value) => value.toLowerCase().includes(query));
  });
});

function scopeLabel(scope: AgentMemoryScope) {
  if (scope === "project") return "当前项目";
  if (scope === "conversation") return "当前会话";
  if (scope === "brand") return "品牌规则";
  return "个人偏好";
}
function categoryLabel(category: string) {
  return categoryOptions.find((item) => item.value === category)?.label || category || "其他";
}
function setScopeFilter(value: "all" | AgentMemoryScope) {
  scopeFilter.value = value;
}
function formatDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}
function editorScopeChanged() {
  if (!editor.value) return;
  if (editor.value.scope === "project" || editor.value.scope === "conversation") {
    editor.value.scopeId = props.conversationId || "";
  } else if (editor.value.scope === "user") {
    editor.value.scopeId = "";
  }
}
function editorCategoryChanged() {
  if (!editor.value || !props.conversationId) return;
  if (["product", "constraint", "decision", "project"].includes(editor.value.category)) {
    editor.value.scope = "project";
    editor.value.scopeId = props.conversationId;
  }
}
function startCreate() {
  editor.value = { id: null, content: props.draft.trim(), category: "preference", scope: "user", scopeId: "" };
  pendingDeleteId.value = null;
  clearPending.value = false;
}
function startEdit(item: AgentMemoryItem) {
  editor.value = { id: item.memoryId, content: item.content, category: item.category, scope: item.scope, scopeId: item.scopeId };
  pendingDeleteId.value = null;
}
function closeEditor() {
  if (!saving.value) editor.value = null;
}
async function load() {
  if (!props.open) return;
  loading.value = true;
  try {
    const projectId = props.conversationId || "";
    const response = await fetchAgentMemories({ conversationId: projectId, projectId });
    items.value = response.items || [];
    emit("countChange", items.value.length);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取记忆失败");
  } finally {
    loading.value = false;
  }
}
async function save() {
  const current = editor.value;
  if (!current || !current.content.trim()) {
    toast.error("请输入记忆内容");
    return;
  }
  saving.value = true;
  try {
    const scopedToCurrent = current.scope === "conversation" || current.scope === "project";
    const body = {
      content: current.content.trim(),
      category: current.category,
      scope: current.scope,
      scopeId: scopedToCurrent ? props.conversationId || current.scopeId : current.scopeId,
      ...(current.id ? {} : { conversationId: props.conversationId || "" }),
    };
    const response = current.id ? await updateAgentMemory(current.id, body) : await createAgentMemory(body);
    items.value = current.id
      ? items.value.map((item) => item.memoryId === current.id ? response.item : item)
      : [response.item, ...items.value];
    emit("countChange", items.value.length);
    editor.value = null;
    toast.success(current.id ? "记忆已更新" : "记忆已保存");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "保存记忆失败");
  } finally {
    saving.value = false;
  }
}
async function remove(item: AgentMemoryItem) {
  if (pendingDeleteId.value !== item.memoryId) {
    pendingDeleteId.value = item.memoryId;
    return;
  }
  try {
    await deleteAgentMemory(item.memoryId);
    items.value = items.value.filter((current) => current.memoryId !== item.memoryId);
    pendingDeleteId.value = null;
    emit("countChange", items.value.length);
    toast.success("记忆已删除");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除记忆失败");
  }
}
async function clearAll() {
  if (!clearPending.value) {
    clearPending.value = true;
    return;
  }
  try {
    await clearAgentMemories();
    items.value = [];
    clearPending.value = false;
    emit("countChange", 0);
    toast.success("记忆已清空");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "清空记忆失败");
  }
}

watch(() => props.open, (open) => {
  if (!open) return;
  search.value = "";
  scopeFilter.value = "all";
  editor.value = null;
  pendingDeleteId.value = null;
  clearPending.value = false;
  void load();
});
</script>

<template>
  <BaseModal :open="open" title="记忆" width-class="max-w-[700px]" @close="emit('close')">
    <div class="grid gap-4 p-5">
      <section class="flex flex-wrap items-center justify-between gap-3 border-b border-black/[0.06] pb-4 dark:border-white/10">
        <div class="flex min-w-0 items-center gap-3">
          <span class="grid size-9 shrink-0 place-items-center rounded-lg bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]"><Brain class="size-4" /></span>
          <div class="min-w-0">
            <h3 class="text-sm font-semibold text-slate-900 dark:text-stone-100">使用已保存记忆</h3>
            <p class="mt-0.5 text-xs text-slate-500 dark:text-stone-400">{{ enabled ? `${items.length} 条记忆可用于当前创作` : '当前创作不会读取记忆' }}</p>
          </div>
        </div>
        <button type="button" role="switch" :aria-checked="enabled" class="relative h-6 w-11 shrink-0 rounded-full transition-colors" :class="enabled ? 'bg-[#315be8]' : 'bg-slate-300 dark:bg-stone-700'" @click="emit('update:enabled', !enabled)">
          <span class="absolute left-0 top-1 size-4 rounded-full bg-white shadow-sm transition-transform" :class="enabled ? 'translate-x-6' : 'translate-x-1'" />
        </button>
      </section>

      <div class="flex flex-wrap items-center gap-2">
        <label class="relative min-w-[220px] flex-1">
          <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
          <input v-model="search" class="studio-input h-10 w-full pl-9 pr-3 text-sm" placeholder="搜索记忆" />
        </label>
        <div class="flex items-center gap-1 rounded-lg bg-slate-100 p-1 dark:bg-white/[0.06]">
          <button v-for="option in scopeFilters" :key="option.value" type="button" class="rounded-md px-2.5 py-1.5 text-xs font-semibold transition" :class="scopeFilter === option.value ? 'bg-white text-slate-900 shadow-sm dark:bg-stone-700 dark:text-stone-100' : 'text-slate-500 hover:text-slate-800 dark:text-stone-400 dark:hover:text-stone-200'" @click="setScopeFilter(option.value)">{{ option.label }}</button>
        </div>
        <button type="button" class="studio-button inline-flex h-10 items-center gap-1.5 rounded-lg bg-[#315be8] px-3 text-xs font-semibold text-white hover:bg-[#244bd0]" @click="startCreate"><Plus class="size-3.5" />新增</button>
      </div>

      <div v-if="editor" class="border-y border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.035] px-1 py-4 dark:bg-[#4F7CFF]/[0.07]">
        <div class="mb-3 flex items-center justify-between gap-3">
          <strong class="text-sm text-slate-900 dark:text-stone-100">{{ editor.id ? '编辑记忆' : '新增记忆' }}</strong>
          <button type="button" class="grid size-8 place-items-center rounded-lg text-slate-400 hover:bg-white/70 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-stone-100" aria-label="取消编辑" @click="closeEditor"><X class="size-4" /></button>
        </div>
        <textarea v-model="editor.content" class="studio-input min-h-24 w-full resize-y p-3 text-sm leading-6" placeholder="例如：汽车详情页优先使用真实道路背景" />
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="grid gap-1.5 text-xs font-semibold text-slate-600 dark:text-stone-300">分类
            <select v-model="editor.category" class="studio-input h-10 px-3 text-sm font-normal" @change="editorCategoryChanged"><option v-for="option in categoryOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select>
          </label>
          <label class="grid gap-1.5 text-xs font-semibold text-slate-600 dark:text-stone-300">使用范围
            <select v-model="editor.scope" class="studio-input h-10 px-3 text-sm font-normal" @change="editorScopeChanged"><option v-for="option in scopeOptions" :key="option.value" :value="option.value" :disabled="option.disabled">{{ option.label }}</option></select>
          </label>
        </div>
        <div class="mt-3 flex justify-end gap-2">
          <button type="button" class="studio-button rounded-lg border border-black/[0.08] px-3 py-2 text-xs font-semibold dark:border-white/10" @click="closeEditor">取消</button>
          <button type="button" class="studio-button inline-flex items-center gap-1.5 rounded-lg bg-[#315be8] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50" :disabled="saving" @click="save"><LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Check v-else class="size-3.5" />保存</button>
        </div>
      </div>

      <div v-if="loading" class="grid min-h-36 place-items-center text-sm text-slate-500 dark:text-stone-400"><LoaderCircle class="size-5 animate-spin" /></div>
      <div v-else-if="!filteredItems.length" class="grid min-h-36 place-items-center border-y border-dashed border-black/[0.08] text-sm text-slate-500 dark:border-white/10 dark:text-stone-400">{{ items.length ? '没有匹配的记忆' : '还没有保存的记忆' }}</div>
      <div v-else class="divide-y divide-black/[0.06] border-y border-black/[0.06] dark:divide-white/10 dark:border-white/10">
        <article v-for="item in filteredItems" :key="item.memoryId" class="group flex items-start gap-3 py-3.5">
          <span class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-white/[0.07] dark:text-stone-400"><Brain class="size-4" /></span>
          <div class="min-w-0 flex-1">
            <p class="whitespace-pre-wrap text-sm leading-6 text-slate-800 dark:text-stone-200">{{ item.content }}</p>
            <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-400 dark:text-stone-500"><span class="rounded bg-slate-100 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-white/[0.07] dark:text-stone-300">{{ categoryLabel(item.category) }}</span><span>{{ scopeLabel(item.scope) }}</span><span v-if="formatDate(item.updatedAt)">{{ formatDate(item.updatedAt) }} 更新</span></div>
          </div>
          <div class="flex shrink-0 items-center gap-1 opacity-100 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100">
            <button type="button" class="grid size-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-white/[0.08] dark:hover:text-stone-100" aria-label="编辑记忆" @click="startEdit(item)"><Pencil class="size-3.5" /></button>
            <button type="button" class="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[11px] font-semibold text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-400/10" :aria-label="pendingDeleteId === item.memoryId ? '确认删除记忆' : '删除记忆'" @click="remove(item)"><Trash2 class="size-3.5" />{{ pendingDeleteId === item.memoryId ? '确认' : '' }}</button>
          </div>
        </article>
      </div>

      <footer class="flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.06] pt-4 dark:border-white/10">
        <span class="text-xs text-slate-400 dark:text-stone-500">{{ items.length }} 条已保存记忆</span>
        <div v-if="clearPending" class="flex items-center gap-2 text-xs text-rose-600 dark:text-rose-300"><span>确认清空全部记忆？</span><button type="button" class="font-semibold hover:underline" @click="clearAll">确认清空</button><button type="button" class="text-slate-500 hover:underline dark:text-stone-400" @click="clearPending = false">取消</button></div>
        <button v-else type="button" class="inline-flex items-center gap-1 text-xs font-semibold text-rose-600 hover:underline disabled:opacity-40 dark:text-rose-300" :disabled="!items.length" @click="clearAll"><Trash2 class="size-3.5" />清空全部</button>
      </footer>
    </div>
  </BaseModal>
</template>
