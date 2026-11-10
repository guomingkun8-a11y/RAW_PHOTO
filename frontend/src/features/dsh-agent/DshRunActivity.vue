<script setup lang="ts">
import { Check, ChevronDown, CircleAlert, CircleStop, Clock3, LoaderCircle } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import type { DshAgentActivity } from "@/features/dsh-agent/api";
import DshSources from "./DshSources.vue";

const props = defineProps<{ activities: DshAgentActivity[]; status: string; timings?: Record<string, number>; hasContent?: boolean; stopping?: boolean; hideSources?: boolean }>();
const running = computed(() => props.status === "streaming");
const elapsedMs = ref(0);
let startedAt = 0;
let timer: ReturnType<typeof setInterval> | undefined;
const failures = computed(() => props.activities.filter((item) => item.status === "error"));
const current = computed(() => props.activities.slice().reverse().find((item) => item.status === "running") || props.activities.at(-1));
const durationLabel = computed(() => {
  const ms = props.timings?.total_ms ?? (running.value ? elapsedMs.value : 0);
  if (ms <= 0) return "";
  const seconds = Math.ceil(ms / 1000);
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
});
const summary = computed(() => {
  if (props.stopping) return "正在停止回答";
  if (props.status === "stopped") return "已停止 · 查看运行过程";
  if (props.status === "error") return "接收中断 · 查看运行过程";
  if (!running.value) return "运行过程";
  if (props.hasContent && current.value?.kind === "answer") return "运行过程";
  return current.value ? activityTitle(current.value) : "已发送，等待运行反馈";
});

watch(running, (isRunning) => {
  if (timer) clearInterval(timer);
  timer = undefined;
  if (!isRunning) {
    elapsedMs.value = props.timings?.total_ms ?? elapsedMs.value;
    return;
  }
  startedAt = Date.now();
  elapsedMs.value = 0;
  timer = setInterval(() => { elapsedMs.value = Date.now() - startedAt; }, 1000);
}, { immediate: true });

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

function activityTitle(activity: DshAgentActivity) {
  return activity.kind === "analysis" ? "分析阶段" : activity.title;
}

function activityStatus(activity: DshAgentActivity) {
  if (activity.status === "stopped") return "已停止";
  if (activity.status === "error") return "失败";
  if (activity.status === "completed") return "已完成";
  return running.value ? "进行中" : "状态未确认";
}
</script>

<template>
  <div class="dsh-run">
    <p v-if="durationLabel" class="dsh-run-timing"><Clock3 class="dsh-run-icon" />{{ running ? '已运行' : '用时' }} {{ durationLabel }}</p>
    <details class="dsh-run-process">
      <summary>
        <span v-if="running && (!hasContent || current?.kind !== 'answer')" class="dsh-thinking-dot" aria-hidden="true" />
        <Clock3 v-else-if="running" class="dsh-run-icon" />
        <CircleStop v-else-if="status === 'stopped'" class="dsh-run-icon" />
        <CircleAlert v-else-if="status === 'error' || failures.length" class="dsh-run-icon dsh-run-error" />
        <Check v-else class="dsh-run-icon" />
        <span :key="summary" class="dsh-run-summary" :class="{ 'is-live': running }">{{ summary }}</span>
        <span v-if="failures.length" class="dsh-run-error">{{ failures.length }} 项失败</span>
        <ChevronDown class="dsh-run-icon dsh-run-chevron" />
      </summary>
      <ol>
        <li v-for="activity in activities" :key="activity.id" :data-activity-id="activity.id">
          <CircleAlert v-if="activity.status === 'error'" class="dsh-run-icon dsh-run-error" />
          <Check v-else-if="activity.status === 'completed'" class="dsh-run-icon" />
          <CircleStop v-else-if="activity.status === 'stopped'" class="dsh-run-icon" />
          <LoaderCircle v-else-if="running" class="dsh-run-icon dsh-run-spinner" />
          <CircleAlert v-else class="dsh-run-icon" />
          <div>
            <div class="dsh-run-stage"><strong>{{ activityTitle(activity) }}</strong><span :class="{ 'dsh-run-error': activity.status === 'error' }">{{ activityStatus(activity) }}</span></div>
            <p v-if="activity.kind !== 'analysis' && activity.detail">{{ activity.detail }}</p>
          </div>
        </li>
      </ol>
    </details>
    <p v-for="failure in failures.filter((item) => item.kind === 'search' || item.kind === 'fetch')" :key="failure.id" class="dsh-run-tool-error" role="status">
      {{ failure.kind === 'search' ? '搜索失败' : '网页抓取失败' }}{{ failure.detail ? `：${failure.detail}` : '' }}
    </p>
    <DshSources v-if="!hideSources" :activities="activities" />
  </div>
</template>

<style scoped>
.dsh-run { margin: 0 0 10px; color: var(--muted-foreground); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.dsh-run-timing { display: inline-flex; align-items: center; gap: 5px; margin: 0 0 3px; color: var(--muted-foreground); font-size: 11px; font-variant-numeric: tabular-nums; }
.dsh-run-process summary { display: flex; align-items: center; gap: 7px; width: fit-content; max-width: 100%; padding: 4px 0; cursor: pointer; list-style: none; }
.dsh-run-process summary::-webkit-details-marker { display: none; }
.dsh-run-process summary:hover { color: var(--foreground); }
.dsh-run-process summary:focus-visible { outline: 2px solid color-mix(in srgb, var(--accent) 80%, var(--foreground)); outline-offset: 3px; border-radius: 3px; }
.dsh-run-summary { min-width: 0; }
.dsh-run-summary.is-live { animation: dsh-status-in 160ms ease-out; }
.dsh-thinking-dot { width: 7px; height: 7px; margin: 0 3.5px; flex: none; border-radius: 50%; background: var(--accent); animation: dsh-thinking-pulse 1.8s ease-in-out infinite; }
@keyframes dsh-thinking-pulse { 50% { opacity: .45; transform: scale(.85); } }
@keyframes dsh-status-in { from { opacity: .5; transform: translateY(2px); } to { opacity: 1; transform: translateY(0); } }
.dsh-run-icon { width: 14px; height: 14px; flex: none; }
.dsh-run-chevron { transition: transform 150ms ease-out; }
.dsh-run-process[open] .dsh-run-chevron { transform: rotate(180deg); }
.dsh-run-process ol { display: grid; gap: 10px; list-style: none; margin: 8px 0 12px; padding: 0; }
.dsh-run-process li { display: flex; align-items: flex-start; gap: 8px; }
.dsh-run-process li > svg { margin-top: 3px; }
.dsh-run-process li > div { min-width: 0; flex: 1; }
.dsh-run-stage { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.dsh-run-stage strong { font-weight: 500; }
.dsh-run-stage span { font-size: 11px; }
.dsh-run-process p { margin: 2px 0 0; white-space: pre-wrap; }
.dsh-run-error,.dsh-run-tool-error { color: color-mix(in srgb, var(--destructive) 65%, var(--foreground)); }
.dsh-run-tool-error { margin: 5px 0; white-space: pre-wrap; }
.dsh-run-spinner { animation: dsh-run-spin 1s linear infinite; }
@keyframes dsh-run-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .dsh-run-spinner,.dsh-thinking-dot,.dsh-run-summary.is-live { animation: none; } .dsh-run-chevron { transition: none; } }
</style>
