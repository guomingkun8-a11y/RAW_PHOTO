<script setup lang="ts">
import { Bell, Eye, Pencil, Plus, RefreshCw, Save, Send, X } from "@lucide/vue";
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import ComposerSelect from "@/components/ComposerSelect.vue";
import {
  createSystemAnnouncement, disableSystemAnnouncement, fetchSystemAnnouncements,
  updateSystemAnnouncement, type SystemAnnouncement,
} from "@/lib/api";

const types = [
  { value: "info", label: "普通通知" },
  { value: "success", label: "功能更新" },
  { value: "warning", label: "重要提醒" },
  { value: "error", label: "故障通知" },
];
const items = ref<SystemAnnouncement[]>([]);
const loading = ref(true);
const saving = ref(false);
const loadError = ref("");
const formError = ref("");
const editorOpen = ref(false);
const editingId = ref<number | null>(null);
const draft = reactive({ title: "", content: "", type: "info", enabled: false });
const titleInput = ref<HTMLInputElement | null>(null);
const editor = ref<HTMLElement | null>(null);
const statusTarget = ref<SystemAnnouncement | null>(null);
const canSave = computed(() => Boolean(draft.title.trim() && draft.content.trim()));

function typeLabel(value: string) {
  return types.find((item) => item.value === value)?.label || "普通通知";
}

function dotClass(value: string) {
  if (value === "success") return "bg-emerald-500";
  if (value === "warning") return "bg-amber-500";
  if (value === "error") return "bg-rose-500";
  return "bg-[#4F7CFF]";
}

async function load() {
  if (saving.value) return;
  loading.value = true;
  loadError.value = "";
  try {
    items.value = (await fetchSystemAnnouncements({ includeDisabled: true, limit: 50 })).items;
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "读取公告失败，请重试";
  } finally {
    loading.value = false;
  }
}

async function openEditor(item?: SystemAnnouncement) {
  editingId.value = item?.id ?? null;
  Object.assign(draft, {
    title: item?.title || "", content: item?.content || "",
    type: item?.type || "info", enabled: item?.enabled ?? false,
  });
  formError.value = "";
  editorOpen.value = true;
  await nextTick();
  editor.value?.scrollIntoView({ block: "nearest" });
  titleInput.value?.focus({ preventScroll: true });
}

function applyItem(item: SystemAnnouncement) {
  items.value = [item, ...items.value.filter((current) => current.id !== item.id)]
    .sort((left, right) => right.created_at.localeCompare(left.created_at) || right.id - left.id)
    .slice(0, 50);
}

async function save(enabled: boolean) {
  if (saving.value || !canSave.value) return;
  saving.value = true;
  formError.value = "";
  try {
    const body = { title: draft.title.trim(), content: draft.content.trim(), type: draft.type, enabled };
    const item = editingId.value === null
      ? await createSystemAnnouncement(body)
      : await updateSystemAnnouncement(editingId.value, body);
    applyItem(item);
    editorOpen.value = false;
    toast.success(enabled ? "公告已发布，更新内容将推送给成员" : "未发布公告已保存");
  } catch (error) {
    formError.value = error instanceof Error ? error.message : "保存失败，请重试";
  } finally {
    saving.value = false;
  }
}

async function changeStatus() {
  const target = statusTarget.value;
  if (!target || saving.value) return;
  saving.value = true;
  try {
    const item = target.enabled
      ? await disableSystemAnnouncement(target.id)
      : await updateSystemAnnouncement(target.id, { enabled: true });
    applyItem(item);
    statusTarget.value = null;
    toast.success(item.enabled ? "公告已发布" : "公告已下架");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "操作失败，请重试");
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section aria-label="公告管理">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-[18px] font-semibold text-slate-900 dark:text-stone-100">更新公告</h2>
        <p class="mt-1 text-[13px] text-slate-600 dark:text-stone-400">发布后通知全体成员；下架后，成员不再看到该公告。</p>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="announcement-button announcement-secondary" :disabled="loading || saving" @click="load"><RefreshCw class="size-4" />刷新</button>
        <button type="button" class="announcement-button announcement-primary" :disabled="editorOpen || saving" @click="openEditor()"><Plus class="size-4" />新增公告</button>
      </div>
    </div>

    <form v-if="editorOpen" ref="editor" class="mt-6 border-y border-black/[0.08] py-5 dark:border-white/10" aria-label="公告编辑" @submit.prevent="save(true)">
      <div class="mb-5 flex items-center justify-between gap-3">
        <h3 class="text-[16px] font-semibold text-slate-900 dark:text-stone-100">{{ editingId === null ? '新增公告' : '编辑公告' }}</h3>
        <button type="button" class="announcement-button announcement-secondary" :disabled="saving" @click="editorOpen = false"><X class="size-4" />取消编辑</button>
      </div>
      <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <fieldset class="min-w-0 space-y-4" :disabled="saving">
          <div>
            <label for="announcement-title" class="announcement-label">公告标题 <span class="text-rose-600">*</span></label>
            <input id="announcement-title" ref="titleInput" v-model="draft.title" class="announcement-input" required maxlength="191" placeholder="例如：图片生成体验更新" />
          </div>
          <div class="max-w-64">
            <span class="announcement-label">公告类型</span>
            <ComposerSelect v-model="draft.type" label="" aria-label="公告类型" :options="types" variant="field" :disabled="saving" />
          </div>
          <div>
            <label for="announcement-content" class="announcement-label">公告正文 <span class="text-rose-600">*</span></label>
            <textarea id="announcement-content" v-model="draft.content" class="announcement-input min-h-44 resize-y leading-6" required rows="7" placeholder="填写本次更新内容、使用提醒或维护通知" />
          </div>
        </fieldset>
        <aside class="min-w-0" aria-label="公告预览">
          <div class="mb-3 flex items-center gap-2 text-[13px] font-medium text-slate-600 dark:text-stone-300"><Eye class="size-4" />成员预览</div>
          <div class="rounded-md bg-slate-50 p-4 dark:bg-white/[0.06]">
            <div class="flex items-start gap-2 text-[14px] font-semibold text-slate-950 dark:text-stone-50">
              <span class="mt-1.5 size-2 shrink-0 rounded-full" :class="dotClass(draft.type)" />
              <span class="min-w-0 break-words">{{ draft.title.trim() || '公告标题' }}</span>
            </div>
            <p class="mt-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-slate-600 dark:text-stone-300">{{ draft.content.trim() || '公告正文将在这里显示。' }}</p>
          </div>
        </aside>
      </div>
      <p v-if="formError" role="alert" class="mt-4 text-[13px] text-rose-600 dark:text-rose-300">{{ formError }}</p>
      <div class="mt-5 flex flex-wrap justify-end gap-2">
        <button v-if="!draft.enabled" type="button" class="announcement-button announcement-secondary" :disabled="saving || !canSave" @click="save(false)"><Save class="size-4" />保存但不发布</button>
        <button type="submit" class="announcement-button announcement-primary" :disabled="saving || !canSave"><Send class="size-4" />{{ saving ? '正在保存…' : draft.enabled ? '保存并发布更新' : '发布公告' }}</button>
      </div>
    </form>

    <div class="mt-6 flex items-center justify-between gap-3 text-[13px] text-slate-600 dark:text-stone-400">
      <span>最近 50 条公告 · 包含未发布与已下架公告</span>
      <span v-if="!loading">{{ items.length }} 条</span>
    </div>
    <div v-if="loadError" role="alert" class="mt-4 flex flex-wrap items-center gap-3 text-[13px] text-rose-600 dark:text-rose-300">
      {{ loadError }}<button type="button" class="underline underline-offset-4" @click="load">重试</button>
    </div>
    <div v-if="loading" role="status" class="mt-4 border-y border-black/[0.08] py-6 text-[13px] text-slate-600 dark:border-white/10 dark:text-stone-400">正在读取公告…</div>
    <div v-else-if="!items.length && !loadError" class="mt-4 flex items-center gap-3 border-y border-black/[0.08] py-8 dark:border-white/10">
      <Bell class="size-5 shrink-0 text-slate-400" />
      <p class="text-[14px] text-slate-600 dark:text-stone-300">暂无公告</p>
    </div>
    <ul v-else-if="items.length" class="mt-3 divide-y divide-black/[0.08] border-y border-black/[0.08] dark:divide-white/10 dark:border-white/10" aria-label="公告列表">
      <li v-for="item in items" :key="item.id" class="flex flex-col gap-3 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 class="min-w-0 break-words text-[14px] font-semibold text-slate-900 dark:text-stone-100">{{ item.title }}</h3>
            <span class="shrink-0 rounded px-1.5 py-0.5 text-[12px]" :class="item.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-stone-300'">{{ item.enabled ? '已发布' : '未发布' }}</span>
          </div>
          <p class="mt-1.5 line-clamp-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-slate-600 dark:text-stone-300">{{ item.content }}</p>
          <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500 dark:text-stone-400">
            <span class="inline-flex items-center gap-1.5"><span class="size-1.5 rounded-full" :class="dotClass(item.type)" />{{ typeLabel(item.type) }}</span>
            <span>更新于 {{ item.updated_at || item.created_at }}</span>
          </div>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <button type="button" class="announcement-button announcement-secondary" :disabled="saving || editorOpen" :aria-label="`编辑${item.title}`" @click="openEditor(item)"><Pencil class="size-3.5" />编辑</button>
          <button type="button" class="announcement-button announcement-secondary" :disabled="saving || editorOpen" :aria-label="`${item.enabled ? '下架' : '发布'}${item.title}`" @click="statusTarget = item">{{ item.enabled ? '下架' : '发布' }}</button>
        </div>
      </li>
    </ul>

    <BaseModal :open="!!statusTarget" :title="statusTarget?.enabled ? '下架公告' : '发布公告'" width-class="max-w-[440px]" :show-close="!saving" @close="!saving && (statusTarget = null)">
      <div class="p-5">
        <p class="break-words text-[14px] leading-6 text-slate-700 dark:text-stone-200">{{ statusTarget?.enabled ? '下架后，所有成员将不再看到此公告，之后可以重新发布。' : '发布后，所有成员均可看到此公告，并收到更新提示。' }}</p>
        <p class="mt-3 break-words text-[14px] font-semibold text-slate-950 dark:text-stone-50">{{ statusTarget?.title }}</p>
        <div class="mt-5 flex justify-end gap-2">
          <button type="button" class="announcement-button announcement-secondary" :disabled="saving" @click="statusTarget = null">取消</button>
          <button type="button" class="announcement-button announcement-primary" :disabled="saving" @click="changeStatus">{{ saving ? '正在处理…' : statusTarget?.enabled ? '确认下架' : '确认发布' }}</button>
        </div>
      </div>
    </BaseModal>
  </section>
</template>

<style scoped>
@reference "../../styles.css";
.announcement-button {
  @apply inline-flex min-h-10 items-center justify-center gap-2 rounded-md px-3 text-[13px] font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#315be8] disabled:cursor-not-allowed disabled:opacity-50;
}
.announcement-primary {
  @apply bg-slate-950 text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-stone-200;
}
.announcement-secondary {
  @apply border border-black/10 text-slate-700 hover:bg-slate-100 dark:border-white/10 dark:text-stone-200 dark:hover:bg-white/[0.06];
}
.announcement-label {
  @apply mb-2 block text-[13px] font-medium text-slate-700 dark:text-stone-200;
}
.announcement-input {
  @apply w-full rounded-md border border-black/15 bg-white px-3 py-2.5 text-[14px] text-slate-950 placeholder:text-slate-500 focus:border-[#315be8] focus:outline-2 focus:outline-[#315be8]/20 disabled:opacity-50 dark:border-white/15 dark:bg-[#171a21] dark:text-stone-100 dark:placeholder:text-stone-400;
}
</style>
