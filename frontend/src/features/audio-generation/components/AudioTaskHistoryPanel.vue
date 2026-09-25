<script setup lang="ts">
import { AudioLines, Download, LoaderCircle, RefreshCw, Trash2 } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import {
  deleteAudioGenerationTask,
  fetchAudioGenerationTasks,
} from "@/features/audio-generation/services/audio-generation-api";
import type { AudioGenerationTask } from "@/features/audio-generation/types/audio-generation";
import { resolveApiAssetUrl } from "@/lib/api";

const props = defineProps<{ active: boolean }>();
const tasks = ref<AudioGenerationTask[]>([]);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const cursor = ref<string | null>(null);
const deleteTarget = ref<AudioGenerationTask | null>(null);
const deletingId = ref("");
let disposed = false;
let refreshTimer = 0;

const hasActiveTasks = computed(() => tasks.value.some((task) => task.status === "queued" || task.status === "running"));

function statusLabel(task: AudioGenerationTask) {
  return {
    queued: "排队中",
    running: "生成中",
    success: "已完成",
    error: "失败",
    canceled: "已取消",
  }[task.status];
}

function statusClass(task: AudioGenerationTask) {
  if (task.status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300";
  if (task.status === "error") return "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
  if (task.status === "canceled") return "bg-slate-100 text-slate-600 dark:bg-white/[0.08] dark:text-stone-300";
  return "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300";
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

function owner(task: AudioGenerationTask) {
  return task.owner_name || task.owner_username || task.owner_id || "未知用户";
}

function resultUrl(task: AudioGenerationTask) {
  return resolveApiAssetUrl(task.audio_url || task.data?.find((item) => item.url)?.url || "");
}

function canDelete(task: AudioGenerationTask) {
  return !["queued", "running"].includes(task.status) && !task.reconciliation_required;
}

async function load(append = false, silent = false) {
  if (loading.value || loadingMore.value || disposed) return;
  if (append && !cursor.value) return;
  if (append) loadingMore.value = true;
  else if (!silent) loading.value = true;
  try {
    const response = await fetchAudioGenerationTasks([], {
      limit: 30,
      cursor: append ? cursor.value || undefined : undefined,
    });
    if (disposed) return;
    const merged = new Map((append ? tasks.value : []).map((task) => [task.id, task]));
    response.items.forEach((task) => merged.set(task.id, task));
    tasks.value = [...merged.values()];
    cursor.value = response.has_more ? response.next_cursor || null : null;
    error.value = "";
  } catch (loadError) {
    if (!disposed) error.value = loadError instanceof Error ? loadError.message : "读取音频记录失败";
  } finally {
    loading.value = false;
    loadingMore.value = false;
    scheduleRefresh();
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer);
  if (!disposed && props.active && hasActiveTasks.value) {
    refreshTimer = window.setTimeout(() => void load(false, true), document.hidden ? 12_000 : 4_000);
  }
}

async function confirmDelete() {
  const task = deleteTarget.value;
  if (!task || !canDelete(task)) return;
  deletingId.value = task.id;
  try {
    await deleteAudioGenerationTask(task.id);
    tasks.value = tasks.value.filter((item) => item.id !== task.id);
    deleteTarget.value = null;
    toast.success("音频记录已永久删除");
  } catch (deleteError) {
    toast.error(deleteError instanceof Error ? deleteError.message : "删除音频记录失败");
  } finally {
    deletingId.value = "";
  }
}

watch(() => props.active, (active) => {
  window.clearTimeout(refreshTimer);
  if (active && !tasks.value.length) void load();
  else if (active) scheduleRefresh();
}, { immediate: true });

onBeforeUnmount(() => {
  disposed = true;
  window.clearTimeout(refreshTimer);
});
</script>

<template>
  <div>
    <p v-if="error" role="alert" class="mb-3 flex items-center justify-between gap-3 text-sm text-rose-600 dark:text-rose-300">
      <span>{{ error }}</span>
      <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="重试" aria-label="重试加载音频记录" @click="load(false)"><RefreshCw class="size-4" /></button>
    </p>
    <div v-if="loading" class="space-y-3">
      <div v-for="index in 4" :key="index" class="studio-skeleton h-28 rounded-xl" />
    </div>
    <div v-else-if="!tasks.length && !error" class="grid min-h-[300px] place-items-center rounded-xl border border-dashed border-slate-300 px-6 text-center dark:border-white/10">
      <div>
        <AudioLines class="mx-auto size-6 text-slate-400" />
        <p class="mt-3 text-sm font-semibold text-slate-900 dark:text-stone-100">暂无音频记录</p>
        <p class="mt-1 text-xs text-slate-500">提交音频生成任务后会显示在这里。</p>
      </div>
    </div>
    <div v-else class="space-y-3">
      <article v-for="task in tasks" :key="task.id" class="rounded-lg border border-black/[0.06] bg-white p-4 dark:border-white/10 dark:bg-white/[0.04]">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-md px-2 py-1 text-[11px] font-semibold" :class="statusClass(task)">{{ statusLabel(task) }}</span>
              <span class="text-xs text-slate-500">{{ task.model }}</span>
              <span v-if="task.mode === 'dialogue'" class="text-xs text-slate-500">双人对话</span>
              <span v-if="formatCost(task.cost)" class="text-xs font-semibold text-[#315be8]">{{ formatCost(task.cost) }}</span>
            </div>
            <p class="mt-2 line-clamp-2 text-sm leading-6 text-slate-700 dark:text-stone-200">{{ task.prompt }}</p>
            <div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
              <span>{{ formatDate(task.updated_at || task.created_at) }}</span>
              <span>用户：{{ owner(task) }}</span>
              <span v-if="task.output_format">{{ task.output_format.toUpperCase() }}</span>
            </div>
            <p v-if="task.error" class="mt-2 line-clamp-2 text-xs text-rose-600 dark:text-rose-300">{{ task.error }}</p>
            <audio v-if="task.status === 'success' && resultUrl(task)" class="mt-3 h-10 w-full" :src="resultUrl(task)" controls preload="none" />
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <a v-if="resultUrl(task)" :href="resultUrl(task)" download class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="下载音频" aria-label="下载音频"><Download class="size-4" /></a>
            <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-lg text-rose-600 disabled:cursor-not-allowed disabled:opacity-35" :disabled="!canDelete(task)" :title="canDelete(task) ? '删除记录' : '任务完成或对账后才能删除'" aria-label="删除音频记录" @click="deleteTarget = task"><Trash2 class="size-4" /></button>
          </div>
        </div>
      </article>
    </div>
    <button v-if="cursor" type="button" class="studio-button mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm" :disabled="loadingMore" @click="load(true)">
      <LoaderCircle v-if="loadingMore" class="size-4 animate-spin" />
      {{ loadingMore ? '加载中' : '加载更早的音频记录' }}
    </button>
  </div>

  <BaseModal :open="Boolean(deleteTarget)" title="永久删除音频记录" description="删除后将移除历史记录并异步清理音频文件，已记录的费用不会删除。" width-class="max-w-[460px]" :show-close="false" @close="deleteTarget = null">
    <div class="space-y-4 p-5">
      <p class="line-clamp-3 text-sm leading-6 text-slate-700 dark:text-stone-200">{{ deleteTarget?.prompt }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-lg border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" :disabled="Boolean(deletingId)" @click="deleteTarget = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="Boolean(deletingId)" @click="confirmDelete">
          <LoaderCircle v-if="deletingId" class="size-4 animate-spin" />
          确认删除
        </button>
      </div>
    </div>
  </BaseModal>
</template>
