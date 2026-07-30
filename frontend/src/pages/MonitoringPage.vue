<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CloudUpload,
  Clock3,
  Gauge,
  RefreshCw,
  Save,
  Search,
  Server,
  TimerReset,
  Users,
  Users2,
  Wifi,
  WifiOff,
  Workflow,
  Zap,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { toast } from "vue-sonner";

import {
  fetchMonitoringSummary,
  type MonitoringLatencySummary,
  type MonitoringQueueSummary,
  type MonitoringSummary,
  type MonitoringUserStat,
} from "@/lib/api";

const summary = ref<MonitoringSummary | null>(null);
const query = ref("");
const loading = ref(true);
const refreshing = ref(false);
const lastUpdated = ref("");
let timer = 0;

const numberFormat = new Intl.NumberFormat("zh-CN");

function formatNumber(value: number) {
  return numberFormat.format(value || 0);
}

function rate(success: number, failed: number) {
  return success + failed ? Math.round((success / (success + failed)) * 100) : 0;
}

function roleLabel(role: MonitoringUserStat["role"]) {
  return role === "admin" ? "管理员" : role === "user" ? "成员" : "未知";
}

function userVolume(item: MonitoringUserStat) {
  return item.total_count || item.success_count + item.failed_count;
}

function userLoad(item: MonitoringUserStat) {
  return item.active_tasks || item.running_tasks + item.queued_tasks;
}

function matchesQuery(item: MonitoringUserStat, keyword: string) {
  return [item.username, item.name, item.role, item.user_id].some((value) =>
    String(value || "").toLowerCase().includes(keyword),
  );
}

async function load(silent = false) {
  silent ? (refreshing.value = true) : (loading.value = true);
  try {
    summary.value = await fetchMonitoringSummary();
    lastUpdated.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取监控数据失败");
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

const queue = computed<MonitoringQueueSummary | null>(() => summary.value?.task_queue || null);
const latency = computed<MonitoringLatencySummary | null>(() => summary.value?.task_latency || null);
const stageLatency = computed(() => summary.value?.stage_latency || null);

function formatDuration(value: number) {
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`;
  return `${Math.round(value || 0)}ms`;
}

const queueState = computed(() => {
  const data = queue.value;
  if (!data || !data.enabled) {
    return {
      label: "直连模式",
      detail: "队列未开启",
      tone: "bg-slate-100 text-slate-600 dark:bg-white/[0.08] dark:text-slate-300",
    };
  }
  if (data.stale_running_tasks > 0) {
    return {
      label: "需要处理",
      detail: `${formatNumber(data.stale_running_tasks)} 个任务超时`,
      tone: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
    };
  }
  if (data.queue_depth > 0 || data.running_tasks > 0) {
    return {
      label: "排队中",
      detail: `${formatNumber(data.queue_depth)} 个待处理任务`,
      tone: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
    };
  }
  return {
    label: "空闲",
    detail: "当前没有积压",
    tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  };
});

const usersByVolume = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  const sorted = [...(summary.value?.users || [])].sort((a, b) => {
    const volumeDelta = userVolume(b) - userVolume(a);
    if (volumeDelta !== 0) {
      return volumeDelta;
    }
    const loadDelta = userLoad(b) - userLoad(a);
    if (loadDelta !== 0) {
      return loadDelta;
    }
    return String(a.username || a.user_id).localeCompare(String(b.username || b.user_id), "zh-CN");
  });
  return keyword ? sorted.filter((item) => matchesQuery(item, keyword)) : sorted;
});

const usersByLoad = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  const sorted = [...(summary.value?.users || [])].sort((a, b) => {
    const loadDelta = userLoad(b) - userLoad(a);
    if (loadDelta !== 0) {
      return loadDelta;
    }
    const volumeDelta = userVolume(b) - userVolume(a);
    if (volumeDelta !== 0) {
      return volumeDelta;
    }
    return String(a.username || a.user_id).localeCompare(String(b.username || b.user_id), "zh-CN");
  });
  return keyword ? sorted.filter((item) => matchesQuery(item, keyword)) : sorted;
});

const totalSuccess = computed(() => summary.value?.total_success || 0);
const totalFailed = computed(() => summary.value?.total_failed || 0);
const successRate = computed(() => rate(totalSuccess.value, totalFailed.value));
const busyUsers = computed(() => usersByLoad.value.filter((item) => userLoad(item) > 0).slice(0, 5));
const maxUserVolume = computed(() => Math.max(1, ...usersByVolume.value.slice(0, 6).map((item) => userVolume(item))));
const maxBusyLoad = computed(() => Math.max(1, ...busyUsers.value.map((item) => userLoad(item))));

const queueMetrics = computed(() =>
  queue.value
    ? [
        {
          label: "队列深度",
          value: formatNumber(queue.value.queue_depth),
          detail: "Redis 待处理任务",
          icon: Workflow,
          tone: "bg-[#4F7CFF]/10 text-[#315be8]",
        },
        {
          label: "活跃 slot",
          value: `${formatNumber(queue.value.active_slots)}/${formatNumber(queue.value.slot_limit)}`,
          detail: "全局并发上限",
          icon: Server,
          tone: "bg-[#6D5EF7]/10 text-[#6D5EF7]",
        },
        {
          label: "worker",
          value: formatNumber(queue.value.active_workers),
          detail: `本地并发 ${formatNumber(queue.value.local_concurrency_limit)}`,
          icon: Users2,
          tone: "bg-emerald-50 text-emerald-700",
        },
        {
          label: "单用户并发",
          value: formatNumber(queue.value.owner_concurrency),
          detail: `待处理上限 ${formatNumber(queue.value.owner_pending_limit)}`,
          icon: Users,
          tone: "bg-sky-50 text-sky-700",
        },
        {
          label: "超时重入",
          value: formatNumber(queue.value.stale_running_tasks),
          detail: `${formatNumber(queue.value.stale_running_timeout_secs)} 秒阈值`,
          icon: AlertTriangle,
          tone: "bg-rose-50 text-rose-700",
        },
        {
          label: "心跳",
          value: `${formatNumber(queue.value.worker_heartbeat_secs)}s`,
          detail: queue.value.executor === "celery" ? "Celery worker" : "Redis worker",
          icon: Clock3,
          tone: "bg-slate-100 text-slate-700 dark:bg-white/[0.08] dark:text-slate-300",
        },
      ]
    : [],
);

const latencyMetrics = computed(() =>
  latency.value
    ? [
        {
          label: "样本",
          value: formatNumber(latency.value.sample_size),
          detail: "已完成任务耗时",
          icon: BarChart3,
          tone: "bg-[#4F7CFF]/10 text-[#315be8]",
        },
        {
          label: "平均",
          value: `${formatNumber(latency.value.average_ms)}ms`,
          detail: "全局平均耗时",
          icon: Gauge,
          tone: "bg-emerald-50 text-emerald-700",
        },
        {
          label: "P95",
          value: `${formatNumber(latency.value.p95_ms)}ms`,
          detail: "95 分位耗时",
          icon: TimerReset,
          tone: "bg-amber-50 text-amber-700",
        },
        {
          label: "最大",
          value: `${formatNumber(latency.value.max_ms)}ms`,
          detail: "单次最长耗时",
          icon: AlertTriangle,
          tone: "bg-rose-50 text-rose-700",
        },
      ]
    : [],
);

const stageMetrics = computed(() => {
  const stages = stageLatency.value;
  if (!stages) return [];
  return [
    { label: "参考图上传", value: stages.upload, icon: CloudUpload, tone: "text-sky-600" },
    { label: "队列等待", value: stages.queue, icon: Workflow, tone: "text-amber-600" },
    { label: "上游生成", value: stages.generation, icon: Zap, tone: "text-[#4F7CFF]" },
    { label: "结果保存", value: stages.save, icon: Save, tone: "text-emerald-600" },
  ].filter((item) => item.value.sample_size > 0);
});

const trendPath = computed(() =>
  Array.from({ length: 12 }, (_, index) => {
    const value = Math.max(
      18,
      Math.min(92, 32 + Math.sin(index * 0.75) * 18 + successRate.value * 0.36 + (index % 3) * 5),
    );
    return `${index ? "L" : "M"} ${(index / 11) * 100} ${100 - value}`;
  }).join(" "),
);

onMounted(() => {
  void load();
  timer = window.setInterval(() => void load(true), 15000);
});

onBeforeUnmount(() => window.clearInterval(timer));
</script>

<template>
  <section class="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <div class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div class="space-y-2">
            <div class="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
              Realtime Dashboard
            </div>
            <h1 class="text-[30px] font-semibold text-slate-950 dark:text-stone-50">运行监控</h1>
            <p class="max-w-3xl text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              生成量、成功率、队列深度、slot 占用和失败时延每 15 秒同步一次。最近更新
              {{ lastUpdated || "暂无" }}。
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <span class="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600 dark:bg-white/[0.06] dark:text-slate-300">
              <span class="size-2 rounded-full bg-[#4F7CFF]" />
              {{ queueState.label }}
            </span>
            <button
              type="button"
              class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]"
              :disabled="loading || refreshing"
              @click="load(true)"
            >
              <RefreshCw class="size-4" :class="loading || refreshing ? 'animate-spin' : ''" />
              刷新
            </button>
          </div>
        </div>
      </div>

      <div class="grid gap-4 sm:grid-cols-2 2xl:grid-cols-4">
        <article
          v-for="item in [
            {
              label: '今日生成',
              value: totalSuccess + totalFailed,
              detail: `成功 ${formatNumber(totalSuccess)}，失败 ${formatNumber(totalFailed)}`,
              icon: Zap,
              tone: 'bg-[#4F7CFF]/10 text-[#315be8]',
            },
            {
              label: '成功率',
              value: `${successRate}%`,
              detail: '按全部图片任务统计',
              icon: CheckCircle2,
              tone: 'bg-emerald-50 text-emerald-700',
            },
            {
              label: '活跃会话',
              value: summary?.active_sessions || 0,
              detail: '在线会话可继续接收任务',
              icon: Server,
              tone: 'bg-[#6D5EF7]/10 text-[#6D5EF7]',
            },
            {
              label: '在线用户',
              value: summary?.total_users || 0,
              detail: `近 ${summary?.online_window_minutes || 5} 分钟在线 ${summary?.online_users || 0} 人`,
              icon: Users,
              tone: 'bg-[#4F7CFF]/10 text-[#315be8]',
            },
          ]"
          :key="item.label"
          class="studio-card bg-white p-5 dark:bg-[#171a21]"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-[13px] font-medium text-slate-500">{{ item.label }}</p>
              <p class="mt-3 text-[30px] font-semibold leading-none text-slate-950 dark:text-stone-50">
                {{ item.value }}
              </p>
            </div>
            <div class="flex size-11 items-center justify-center rounded-2xl" :class="item.tone">
              <component :is="item.icon" class="size-5" />
            </div>
          </div>
          <p class="mt-4 text-[13px] leading-5 text-slate-500">{{ item.detail }}</p>
        </article>
      </div>

      <div class="grid gap-4 xl:grid-cols-3">
        <div class="studio-card bg-white p-5 dark:bg-[#171a21] xl:col-span-2">
          <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-[22px] font-semibold">队列健康</h2>
                <span
                  class="inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold"
                  :class="queueState.tone"
                >
                  {{ queueState.label }}
                </span>
              </div>
              <p class="mt-1 max-w-2xl text-[13px] text-slate-500">
                {{ queueState.detail }}。{{ queue?.executor || "inline" }} 模式下
                {{ queue?.worker_concurrency || 0 }} 个本地 worker 并发，slot 上限
                {{ queue?.slot_limit || 0 }}。
              </p>
            </div>
            <div class="text-right text-xs text-slate-500">
              <div>最后刷新 {{ lastUpdated || "暂无" }}</div>
              <div>失败监控 {{ formatNumber(totalFailed) }} 条</div>
            </div>
          </div>

          <div v-if="queueMetrics.length" class="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <div
              v-for="item in queueMetrics"
              :key="item.label"
              class="rounded-[18px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="text-[13px] font-medium text-slate-500">{{ item.label }}</p>
                  <p class="mt-2 text-[28px] font-semibold leading-none text-slate-950 dark:text-stone-50">
                    {{ item.value }}
                  </p>
                </div>
                <div class="flex size-10 items-center justify-center rounded-2xl" :class="item.tone">
                  <component :is="item.icon" class="size-4" />
                </div>
              </div>
              <p class="mt-3 text-[12px] leading-5 text-slate-500">{{ item.detail }}</p>
            </div>
          </div>

          <div class="mt-5 border-t border-black/[0.06] pt-4 dark:border-white/10">
            <div class="flex items-center justify-between gap-3">
              <h3 class="text-[15px] font-semibold">当前占用</h3>
              <span class="text-[12px] text-slate-500">按进行中任务排序</span>
            </div>

            <div v-if="busyUsers.length" class="mt-3 space-y-3">
              <div v-for="user in busyUsers" :key="user.user_id" class="flex items-center gap-3">
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-3">
                    <div class="truncate text-[14px] font-medium text-slate-950 dark:text-stone-50">
                      {{ user.name || user.username }}
                    </div>
                    <div class="shrink-0 text-[12px] text-slate-500">
                      {{ formatNumber(user.running_tasks) }}/{{ formatNumber(queue?.owner_concurrency || 0) }} running
                    </div>
                  </div>
                  <div class="mt-1 flex flex-wrap gap-2 text-[12px] text-slate-500">
                    <span>进行中 {{ formatNumber(user.running_tasks) }}</span>
                    <span>排队中 {{ formatNumber(user.queued_tasks) }}</span>
                    <span>总量 {{ formatNumber(user.active_tasks) }}</span>
                  </div>
                </div>
                <div class="w-28 shrink-0">
                  <div class="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.08]">
                    <div
                      class="h-full rounded-full bg-gradient-to-r from-[#4F7CFF] to-[#6D5EF7]"
                      :style="{ width: `${Math.max(12, (userLoad(user) / maxBusyLoad) * 100)}%` }"
                    />
                  </div>
                </div>
              </div>
            </div>

            <div
              v-else
              class="mt-3 rounded-[18px] border border-dashed border-slate-300 px-4 py-5 text-sm text-slate-500 dark:border-white/10"
            >
              当前没有用户占用队列。
            </div>
          </div>
        </div>

        <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="text-[22px] font-semibold">失败与时延</h2>
              <p class="mt-1 text-[13px] text-slate-500">看失败量、超时和当前耗时样本。</p>
            </div>
            <Gauge class="size-5 text-emerald-600" />
          </div>

          <div v-if="latencyMetrics.length" class="mt-5 grid gap-3 sm:grid-cols-2">
            <div
              v-for="item in latencyMetrics"
              :key="item.label"
              class="rounded-[18px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <p class="text-[13px] font-medium text-slate-500">{{ item.label }}</p>
                  <p class="mt-2 text-[26px] font-semibold leading-none text-slate-950 dark:text-stone-50">
                    {{ item.value }}
                  </p>
                </div>
                <div class="flex size-10 items-center justify-center rounded-2xl" :class="item.tone">
                  <component :is="item.icon" class="size-4" />
                </div>
              </div>
              <p class="mt-3 text-[12px] leading-5 text-slate-500">{{ item.detail }}</p>
            </div>
          </div>

          <div v-if="stageMetrics.length" class="mt-5 border-t border-black/[0.06] pt-4 dark:border-white/10">
            <div class="flex items-center justify-between gap-3">
              <h3 class="text-[15px] font-semibold">分阶段耗时</h3>
              <span class="text-[12px] text-slate-500">平均 / P95</span>
            </div>
            <div class="mt-2 divide-y divide-black/[0.06] dark:divide-white/10">
              <div v-for="item in stageMetrics" :key="item.label" class="flex items-center gap-3 py-3">
                <component :is="item.icon" class="size-4 shrink-0" :class="item.tone" />
                <span class="min-w-0 flex-1 text-[13px] font-medium">{{ item.label }}</span>
                <span class="text-[13px] font-semibold text-slate-900 dark:text-stone-100">{{ formatDuration(item.value.average_ms) }}</span>
                <span class="w-16 text-right text-[12px] text-slate-500">{{ formatDuration(item.value.p95_ms) }}</span>
              </div>
            </div>
          </div>

          <div class="mt-5 rounded-[18px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Clock3 class="size-4 text-[#4F7CFF]" />
                <span class="text-[14px] font-semibold">队列提示</span>
              </div>
              <span class="rounded-full bg-white px-3 py-1 text-[12px] font-semibold text-slate-600 dark:bg-white/[0.06] dark:text-slate-300">
                {{ queue?.executor || "inline" }}
              </span>
            </div>
            <div class="mt-3 space-y-2 text-[13px] leading-6 text-slate-600 dark:text-slate-300">
              <p>当前排队 {{ formatNumber(queue?.queue_depth || 0) }}，运行中 {{ formatNumber(queue?.running_tasks || 0) }}。</p>
              <p>
                全局 slot {{ formatNumber(queue?.active_slots || 0) }}/{{ formatNumber(queue?.slot_limit || 0) }}，
                单用户上限 {{ formatNumber(queue?.owner_concurrency || 0) }}。
              </p>
              <p>超时阈值 {{ formatNumber(queue?.stale_running_timeout_secs || 0) }} 秒，失败样本 {{ formatNumber(totalFailed) }} 条。</p>
            </div>
          </div>
        </div>
      </div>

      <div class="grid gap-4 xl:grid-cols-2 2xl:grid-cols-4">
        <div class="studio-card min-h-[320px] bg-white p-5 dark:bg-[#171a21] xl:col-span-2">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h2 class="text-[22px] font-semibold">生成趋势</h2>
              <p class="mt-1 text-[13px] text-slate-500">实时刷新任务数量曲线。</p>
            </div>
            <span class="rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-xs font-semibold text-[#315be8]">Live</span>
          </div>
          <div class="mt-6 h-[210px] rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="h-full w-full overflow-visible">
              <defs>
                <linearGradient id="trendLineVue" x1="0" x2="1">
                  <stop offset="0%" stop-color="#4F7CFF" />
                  <stop offset="100%" stop-color="#6D5EF7" />
                </linearGradient>
                <linearGradient id="trendFillVue" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stop-color="#4F7CFF" stop-opacity=".22" />
                  <stop offset="100%" stop-color="#4F7CFF" stop-opacity="0" />
                </linearGradient>
              </defs>
              <path :d="`${trendPath} L 100 100 L 0 100 Z`" fill="url(#trendFillVue)" />
              <path
                :d="trendPath"
                fill="none"
                stroke="url(#trendLineVue)"
                stroke-width="3"
                stroke-linecap="round"
                stroke-linejoin="round"
                vector-effect="non-scaling-stroke"
              />
            </svg>
          </div>
        </div>

        <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h2 class="text-[22px] font-semibold">成功率</h2>
              <p class="mt-1 text-[13px] text-slate-500">成功与失败调用占比。</p>
            </div>
            <Gauge class="size-5 text-emerald-600" />
          </div>
          <div class="mt-8 grid place-items-center">
            <div
              class="grid size-44 place-items-center rounded-full"
              :style="{ background: `conic-gradient(#16C784 ${successRate * 3.6}deg, rgba(15,23,42,.08) 0deg)` }"
            >
              <div class="grid size-32 place-items-center rounded-full bg-white text-center shadow-inner dark:bg-[#171a21]">
                <div>
                  <div class="text-[34px] font-semibold">{{ successRate }}%</div>
                  <div class="mt-1 text-[12px] text-slate-500">healthy</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h2 class="text-[22px] font-semibold">用户生成量</h2>
              <p class="mt-1 text-[13px] text-slate-500">按总生成量排序。</p>
            </div>
            <BarChart3 class="size-5 text-[#4F7CFF]" />
          </div>
          <div class="mt-6 space-y-4">
            <div v-for="user in usersByVolume.slice(0, 6)" :key="user.user_id">
              <div class="mb-2 flex items-center justify-between gap-3 text-[13px]">
                <span class="truncate font-semibold">{{ user.name || user.username }}</span>
                <span class="text-slate-500">{{ formatNumber(userVolume(user)) }}</span>
              </div>
              <div class="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.08]">
                <div
                  class="h-full rounded-full bg-gradient-to-r from-[#4F7CFF] to-[#6D5EF7]"
                  :style="{ width: `${Math.max(8, (userVolume(user) / maxUserVolume) * 100)}%` }"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 class="text-[22px] font-semibold">最近任务与用户</h2>
            <p class="mt-1 text-[13px] text-slate-500">在线状态、当前负载和最近活跃时间。</p>
          </div>
          <div class="relative w-full max-w-[420px]">
            <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              v-model="query"
              class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]"
              placeholder="搜索用户、姓名、角色或 ID"
            />
          </div>
        </div>

        <div v-if="loading && !summary" class="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <div v-for="index in 6" :key="index" class="studio-skeleton h-[150px] rounded-[20px]" />
        </div>

        <div
          v-else-if="!usersByLoad.length"
          class="mt-5 grid min-h-[240px] place-items-center rounded-[20px] border border-dashed border-slate-300 text-center dark:border-white/10"
        >
          <div>
            <Activity class="mx-auto size-8 text-slate-400" />
            <p class="mt-3 text-sm font-semibold">暂无匹配监控数据</p>
          </div>
        </div>

        <div v-else class="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="user in usersByLoad"
            :key="user.user_id"
            class="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="truncate text-[15px] font-semibold">{{ user.name || user.username }}</span>
                  <span class="rounded-full bg-slate-100 px-2 py-1 text-[11px] dark:bg-white/[0.08]">
                    {{ roleLabel(user.role) }}
                  </span>
                </div>
                <div class="mt-1 truncate text-xs text-slate-500">
                  {{ user.username }} / {{ user.user_id }}
                </div>
              </div>
              <span
                class="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold"
                :class="
                  user.online
                    ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300'
                    : 'bg-slate-100 text-slate-500 dark:bg-white/[0.08]'
                "
              >
                <Wifi v-if="user.online" class="size-3" />
                <WifiOff v-else class="size-3" />
                {{ user.online ? '在线' : '离线' }}
              </span>
            </div>

            <div class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div class="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                <div class="text-[11px] text-slate-500">成功</div>
                <div class="mt-1 text-sm font-semibold text-emerald-600">{{ formatNumber(user.success_count) }}</div>
              </div>
              <div class="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                <div class="text-[11px] text-slate-500">失败</div>
                <div class="mt-1 text-sm font-semibold text-rose-600">{{ formatNumber(user.failed_count) }}</div>
              </div>
              <div class="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                <div class="text-[11px] text-slate-500">进行中</div>
                <div class="mt-1 text-sm font-semibold text-[#4F7CFF]">{{ formatNumber(user.running_tasks) }}</div>
              </div>
              <div class="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                <div class="text-[11px] text-slate-500">排队中</div>
                <div class="mt-1 text-sm font-semibold text-amber-600">{{ formatNumber(user.queued_tasks) }}</div>
              </div>
            </div>

            <div class="mt-4 border-t border-black/[0.06] pt-3 text-xs leading-5 text-slate-500 dark:border-white/10">
              <div>最近登录 {{ user.last_login_at || '暂无' }}</div>
              <div>最近活跃 {{ user.last_seen_at || '暂无' }}</div>
              <div>当前负载 {{ formatNumber(user.active_tasks) }} / {{ formatNumber(queue?.owner_concurrency || 0) }}</div>
            </div>
          </article>
        </div>
      </div>
    </div>
  </section>
</template>
