<script setup lang="ts">
import { Ban, Download, LoaderCircle, Play, RefreshCw, RotateCcw, Trash2, X } from '@lucide/vue';

import type { CompositionTask } from '@/features/video-timeline/types/timeline';
import { formatTimelineBytes, formatTimelineTime } from '@/features/video-timeline/composables/useTimeline';

defineProps<{
  tasks: CompositionTask[];
  loading?: boolean;
  deletingId?: string;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
  preview: [task: CompositionTask];
  download: [task: CompositionTask];
  remove: [task: CompositionTask];
  cancel: [task: CompositionTask];
  retry: [task: CompositionTask];
}>();

function resultUrl(task: CompositionTask) {
  return task.result_url || task.resultUrl || '';
}
</script>

<template>
  <aside class="timeline-task-panel" aria-label="合成记录">
    <header>
      <div>
        <strong>合成记录</strong>
        <small>{{ tasks.length }} 条</small>
      </div>
      <button class="timeline-icon-button" type="button" title="刷新记录" aria-label="刷新记录" :disabled="loading" @click="emit('refresh')">
        <LoaderCircle v-if="loading" class="timeline-spin" :size="16" />
        <RefreshCw v-else :size="16" />
      </button>
      <button class="timeline-icon-button" type="button" title="关闭记录" aria-label="关闭记录" @click="emit('close')"><X :size="16" /></button>
    </header>
    <div class="timeline-task-list">
      <div v-if="loading && !tasks.length" class="timeline-inline-state"><LoaderCircle class="timeline-spin" :size="16" /> 正在加载</div>
      <div v-else-if="!tasks.length" class="timeline-inline-state">还没有合成记录</div>
      <article v-for="task in tasks" :key="task.id" class="timeline-task-row" :data-status="task.status">
        <button class="timeline-task-main" type="button" :disabled="!resultUrl(task)" @click="emit('preview', task)">
          <span class="timeline-task-status"><i />{{ task.progress || task.status }}</span>
          <small>{{ task.duration ? formatTimelineTime(task.duration) : '--:--' }}<template v-if="task.size"> · {{ formatTimelineBytes(task.size) }}</template></small>
          <span v-if="task.error" class="timeline-task-error" :title="task.error">{{ task.error }}</span>
        </button>
        <div class="timeline-task-actions">
          <button v-if="resultUrl(task)" class="timeline-icon-button" type="button" title="预览成片" aria-label="预览成片" @click="emit('preview', task)"><Play :size="15" /></button>
          <button v-if="resultUrl(task)" class="timeline-icon-button" type="button" title="下载成片" aria-label="下载成片" @click="emit('download', task)"><Download :size="15" /></button>
          <button v-if="task.status === 'queued'" class="timeline-icon-button timeline-task-delete" type="button" title="取消任务" aria-label="取消合成任务" @click="emit('cancel', task)"><Ban :size="15" /></button>
          <button v-if="['error', 'canceled'].includes(task.status)" class="timeline-icon-button" type="button" title="重新合成" aria-label="重新合成" @click="emit('retry', task)"><RotateCcw :size="15" /></button>
          <button class="timeline-icon-button timeline-task-delete" type="button" title="永久删除" aria-label="永久删除合成记录" :disabled="deletingId === task.id || ['queued', 'running'].includes(task.status)" @click="emit('remove', task)">
            <LoaderCircle v-if="deletingId === task.id" class="timeline-spin" :size="15" />
            <Trash2 v-else :size="15" />
          </button>
        </div>
      </article>
    </div>
  </aside>
</template>
