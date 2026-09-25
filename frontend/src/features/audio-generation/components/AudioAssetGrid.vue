<script setup lang="ts">
import { AudioLines, ChevronLeft, ChevronRight, Download, LoaderCircle, RefreshCw, Trash2 } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import {
  deleteAudioGenerationTask,
  fetchAudioGenerationTasks,
} from "@/features/audio-generation/services/audio-generation-api";
import type { AudioGenerationTask } from "@/features/audio-generation/types/audio-generation";
import { resolveApiAssetUrl } from "@/lib/api";

const props = defineProps<{
  active: boolean;
  query: string;
  allOwners: boolean;
  ownerId: string;
}>();

const PAGE_SIZE = 12;
const tasks = ref<AudioGenerationTask[]>([]);
const total = ref(0);
const page = ref(1);
const cursors = ref<string[]>([""]);
const hasMore = ref(false);
const loading = ref(false);
const error = ref("");
const deleteTarget = ref<AudioGenerationTask | null>(null);
const deleting = ref(false);
let requestId = 0;
let filterTimer = 0;

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));
const pageStart = computed(() => tasks.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0);
const pageEnd = computed(() => (page.value - 1) * PAGE_SIZE + tasks.value.length);

function resultUrl(task: AudioGenerationTask) {
  return resolveApiAssetUrl(task.audio_url || task.data?.find((item) => item.url)?.url || "");
}

function owner(task: AudioGenerationTask) {
  return task.owner_name || task.owner_username || task.owner_id || "未知用户";
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function formatCost(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value)
    ? `￥${value.toLocaleString("zh-CN", { maximumFractionDigits: 6 })}`
    : "";
}

async function load(targetPage = page.value) {
  if (!props.active) return;
  const cursor = targetPage === 1 ? "" : cursors.value[targetPage - 1];
  if (cursor === undefined) return;
  const currentRequest = ++requestId;
  loading.value = true;
  try {
    const response = await fetchAudioGenerationTasks([], {
      limit: PAGE_SIZE,
      cursor: cursor || undefined,
      status: "success",
      q: props.query.trim() || undefined,
      allOwners: props.allOwners,
      ownerId: props.ownerId || undefined,
    });
    if (currentRequest !== requestId) return;
    tasks.value = response.items.filter((task) => Boolean(resultUrl(task)));
    total.value = Number(response.total || 0);
    hasMore.value = Boolean(response.has_more);
    page.value = targetPage;
    if (response.has_more && response.next_cursor) cursors.value[targetPage] = response.next_cursor;
    error.value = "";
  } catch (loadError) {
    if (currentRequest === requestId) error.value = loadError instanceof Error ? loadError.message : "读取音频资产失败";
  } finally {
    if (currentRequest === requestId) loading.value = false;
  }
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await deleteAudioGenerationTask(deleteTarget.value.id);
    deleteTarget.value = null;
    toast.success("音频记录已永久删除");
    await load(tasks.value.length === 1 && page.value > 1 ? page.value - 1 : page.value);
  } catch (deleteError) {
    toast.error(deleteError instanceof Error ? deleteError.message : "删除音频失败");
  } finally {
    deleting.value = false;
  }
}

watch(() => props.active, (active) => {
  if (active) void load(1);
}, { immediate: true });
watch(() => [props.query, props.allOwners, props.ownerId] as const, () => {
  window.clearTimeout(filterTimer);
  cursors.value = [""];
  filterTimer = window.setTimeout(() => void load(1), 250);
});
onBeforeUnmount(() => {
  requestId += 1;
  window.clearTimeout(filterTimer);
});
</script>

<template>
  <div class="space-y-4">
    <div v-if="error" role="alert" class="flex items-center justify-between gap-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-400/10 dark:text-rose-300">
      <span>{{ error }}</span>
      <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="重试" aria-label="重试加载音频资产" @click="load(page)"><RefreshCw class="size-4" /></button>
    </div>

    <div v-if="loading && !tasks.length" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <div v-for="index in 6" :key="index" class="studio-skeleton h-52 rounded-lg" />
    </div>
    <div v-else-if="!tasks.length && !error" class="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
      <div>
        <div class="mx-auto flex size-12 items-center justify-center rounded-lg bg-slate-950 text-white dark:bg-white dark:text-slate-950"><AudioLines class="size-5" /></div>
        <h2 class="mt-4 text-lg font-semibold">{{ query ? '没有匹配的音频' : '暂无音频资产' }}</h2>
        <p class="mt-1 text-sm text-slate-500">{{ query ? '换一个关键词试试。' : '完成音频生成后，结果会自动出现在这里。' }}</p>
      </div>
    </div>
    <div v-else class="grid items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3">
      <article v-for="task in tasks" :key="task.id" class="studio-card flex min-h-56 flex-col bg-white p-4 dark:bg-[#171a21]">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2 text-[11px]">
              <span class="inline-flex items-center gap-1 rounded-md bg-[#4F7CFF]/10 px-2 py-1 font-semibold text-[#315be8]"><AudioLines class="size-3" />音频</span>
              <span class="rounded-md bg-slate-100 px-2 py-1 text-slate-500 dark:bg-white/[0.08]">{{ task.model }}</span>
              <span v-if="task.mode === 'dialogue'" class="rounded-md bg-slate-100 px-2 py-1 text-slate-500 dark:bg-white/[0.08]">双人对话</span>
            </div>
            <p class="mt-3 line-clamp-3 min-h-[60px] text-sm leading-5 text-slate-700 dark:text-stone-200">{{ task.prompt }}</p>
          </div>
          <button type="button" class="studio-button inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-rose-600" title="删除音频" aria-label="删除音频" @click="deleteTarget = task"><Trash2 class="size-4" /></button>
        </div>
        <audio class="mt-4 h-10 w-full" :src="resultUrl(task)" controls preload="none" />
        <div class="mt-auto flex items-end justify-between gap-3 pt-4 text-[11px] text-slate-500">
          <div class="min-w-0">
            <p>{{ formatDate(task.created_at) }}</p>
            <p class="mt-1 truncate" :title="owner(task)">用户：{{ owner(task) }}</p>
          </div>
          <div class="flex items-center gap-2">
            <span v-if="formatCost(task.cost)" class="font-semibold text-[#315be8]">{{ formatCost(task.cost) }}</span>
            <a :href="resultUrl(task)" :download="`audio-${task.id}.${task.output_format || 'mp3'}`" class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="下载音频" aria-label="下载音频"><Download class="size-4" /></a>
          </div>
        </div>
      </article>
    </div>

    <div v-if="tasks.length" class="studio-card flex flex-col gap-3 bg-white px-4 py-3 dark:bg-[#171a21] sm:flex-row sm:items-center sm:justify-between">
      <span class="text-sm text-slate-500">第 {{ page }} / {{ totalPages }} 页，显示 {{ pageStart }}-{{ pageEnd }} / {{ total }} 条音频，每页 {{ PAGE_SIZE }} 条</span>
      <div class="flex items-center gap-2">
        <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-lg border border-black/[0.06] disabled:opacity-40 dark:border-white/10" :disabled="loading || page <= 1" aria-label="上一页" @click="load(page - 1)"><ChevronLeft class="size-4" /></button>
        <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-lg border border-black/[0.06] disabled:opacity-40 dark:border-white/10" :disabled="loading || !hasMore" aria-label="下一页" @click="load(page + 1)"><ChevronRight class="size-4" /></button>
      </div>
    </div>
  </div>

  <BaseModal :open="Boolean(deleteTarget)" title="确认删除音频" description="删除后将移除历史记录并异步清理音频文件，费用记录会继续保留。" width-class="max-w-[460px]" :show-close="false" @close="deleteTarget = null">
    <div class="space-y-4 p-5">
      <p class="line-clamp-3 text-sm leading-6 text-slate-700 dark:text-stone-200">{{ deleteTarget?.prompt }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-lg border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" :disabled="deleting" @click="deleteTarget = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="deleting" @click="confirmDelete"><LoaderCircle v-if="deleting" class="size-4 animate-spin" />确认删除</button>
      </div>
    </div>
  </BaseModal>
</template>
