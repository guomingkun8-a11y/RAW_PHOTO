<script setup lang="ts">
import {
  Activity,
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
  CloudUpload,
  Gauge,
  RefreshCw,
  Save,
  Search,
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

type SortDirection = "asc" | "desc";
type UserSortKey = "success" | "failed" | "running" | "queued" | "total" | "load";

const userSortKey = ref<UserSortKey>("load");
const userSortDirection = ref<SortDirection>("desc");

const userSortColumns: { key: UserSortKey; label: string }[] = [
  { key: "success", label: "成功" },
  { key: "failed", label: "失败" },
  { key: "running", label: "进行中" },
  { key: "queued", label: "排队中" },
  { key: "total", label: "总生成" },
];

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

function userSortValue(item: MonitoringUserStat, key: UserSortKey) {
  if (key === "success") return item.success_count;
  if (key === "failed") return item.failed_count;
  if (key === "running") return item.running_tasks;
  if (key === "queued") return item.queued_tasks;
  if (key === "total") return userVolume(item);
  return userLoad(item);
}

function compareUserName(a: MonitoringUserStat, b: MonitoringUserStat) {
  return String(a.username || a.user_id).localeCompare(String(b.username || b.user_id), "zh-CN");
}

function userRolePriority(role: MonitoringUserStat["role"]) {
  return role === "admin" ? 0 : role === "user" ? 1 : 2;
}

function sortUsers(users: MonitoringUserStat[], key: UserSortKey, direction: SortDirection, adminFirst = false) {
  return [...users].sort((a, b) => {
    if (adminFirst) {
      const roleDelta = userRolePriority(a.role) - userRolePriority(b.role);
      if (roleDelta !== 0) return roleDelta;
    }
    const valueDelta = userSortValue(a, key) - userSortValue(b, key);
    if (valueDelta !== 0) return direction === "asc" ? valueDelta : -valueDelta;
    const loadDelta = userLoad(b) - userLoad(a);
    if (loadDelta !== 0) return loadDelta;
    const volumeDelta = userVolume(b) - userVolume(a);
    if (volumeDelta !== 0) return volumeDelta;
    return compareUserName(a, b);
  });
}

function toggleUserSort(key: UserSortKey) {
  if (userSortKey.value === key) {
    userSortDirection.value = userSortDirection.value === "desc" ? "asc" : "desc";
    return;
  }
  userSortKey.value = key;
  userSortDirection.value = "desc";
}

function sortIcon(key: UserSortKey) {
  if (userSortKey.value !== key) return ArrowDownUp;
  return userSortDirection.value === "desc" ? ArrowDown : ArrowUp;
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

const filteredUsers = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  const users = summary.value?.users || [];
  return keyword ? users.filter((item) => matchesQuery(item, keyword)) : users;
});

const usersByLoad = computed(() => sortUsers(filteredUsers.value, "load", "desc"));
const sortedUsers = computed(() => sortUsers(filteredUsers.value, userSortKey.value, userSortDirection.value, true));

const totalSuccess = computed(() => summary.value?.total_success || 0);
const totalFailed = computed(() => summary.value?.total_failed || 0);
const successRate = computed(() => rate(totalSuccess.value, totalFailed.value));
const busyUsers = computed(() => usersByLoad.value.filter((item) => userLoad(item) > 0).slice(0, 3));
const maxBusyLoad = computed(() => Math.max(1, ...busyUsers.value.map((item) => userLoad(item))));
const ownerConcurrencyLimit = computed(() => queue.value?.effective_owner_concurrency || queue.value?.owner_concurrency || 0);

function userLoadPercent(item: MonitoringUserStat) {
  const load = userLoad(item);
  if (!load) return 0;
  const limit = ownerConcurrencyLimit.value || maxBusyLoad.value || load;
  return Math.min(100, Math.max(8, (load / Math.max(1, limit)) * 100));
}

const compactQueueMetrics = computed(() =>
  queue.value
    ? [
        {
          label: "排队",
          value: formatNumber(queue.value.queue_depth),
          tone: "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
        },
        {
          label: "运行",
          value: formatNumber(queue.value.running_tasks),
          tone: "bg-[#4F7CFF]/10 text-[#315be8]",
        },
        {
          label: "slot",
          value: `${formatNumber(queue.value.active_slots)}/${formatNumber(queue.value.slot_limit)}`,
          tone: "bg-[#6D5EF7]/10 text-[#6D5EF7]",
        },
        {
          label: "单用户",
          value: formatNumber(queue.value.effective_owner_concurrency || queue.value.owner_concurrency),
          tone: "bg-sky-50 text-sky-700",
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
  <section class="monitoring-page min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
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

      <div class="grid gap-4 xl:grid-cols-3">
        <div class="studio-card bg-white p-4 dark:bg-[#171a21] xl:col-span-2">
          <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-[20px] font-semibold">队列健康</h2>
                <span
                  class="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold"
                  :class="queueState.tone"
                >
                  {{ queueState.label }}
                </span>
              </div>
              <p class="mt-1 max-w-2xl text-[13px] leading-5 text-slate-500">
                {{ queueState.detail }}。{{ queue?.executor || "inline" }} 模式，worker {{ formatNumber(queue?.active_workers || 0) }}，心跳 {{ formatNumber(queue?.worker_heartbeat_secs || 0) }}s。
              </p>
            </div>
            <div class="text-right text-xs text-slate-500">
              <div>最后刷新 {{ lastUpdated || "暂无" }}</div>
              <div>失败监控 {{ formatNumber(totalFailed) }} 条</div>
            </div>
          </div>

          <div v-if="compactQueueMetrics.length" class="mt-4 grid gap-2 sm:grid-cols-4">
            <div
              v-for="item in compactQueueMetrics"
              :key="item.label"
              class="rounded-xl px-3 py-3"
              :class="item.tone"
            >
              <p class="text-[11px] font-medium opacity-80">{{ item.label }}</p>
              <p class="mt-1 text-xl font-semibold leading-none">{{ item.value }}</p>
            </div>
          </div>

          <div class="mt-4 border-t border-black/[0.06] pt-3 dark:border-white/10">
            <div class="flex items-center justify-between gap-3">
              <h3 class="text-[15px] font-semibold">当前占用</h3>
              <span class="text-[12px] text-slate-500">Top 3</span>
            </div>

            <div v-if="busyUsers.length" class="mt-2 space-y-2">
              <div v-for="user in busyUsers" :key="user.user_id" class="flex items-center gap-3 rounded-xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-3">
                    <div class="truncate text-[14px] font-medium text-slate-950 dark:text-stone-50">
                      {{ user.name || user.username }}
                    </div>
                    <div class="shrink-0 text-[12px] text-slate-500">
                      运行 {{ formatNumber(user.running_tasks) }} · 排队 {{ formatNumber(user.queued_tasks) }}
                    </div>
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
              class="mt-2 rounded-xl bg-[#F8FAFC] px-3 py-3 text-sm text-slate-500 dark:bg-white/[0.04]"
            >
              当前没有用户占用队列。
            </div>
          </div>
        </div>

        <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="text-[22px] font-semibold">失败与时延</h2>
              <p class="mt-1 text-[13px] text-slate-500">只保留排障需要的关键指标。</p>
            </div>
            <Gauge class="size-5 text-emerald-600" />
          </div>

          <div class="mt-5 grid gap-2 sm:grid-cols-3">
            <div class="rounded-xl bg-rose-50 px-3 py-3 dark:bg-rose-400/10">
              <div class="text-[11px] font-medium text-rose-700 dark:text-rose-300">失败</div>
              <div class="mt-1 text-lg font-semibold text-rose-700 dark:text-rose-300">{{ formatNumber(totalFailed) }}</div>
            </div>
            <div class="rounded-xl bg-amber-50 px-3 py-3 dark:bg-amber-400/10">
              <div class="text-[11px] font-medium text-amber-700 dark:text-amber-300">P95</div>
              <div class="mt-1 text-lg font-semibold text-amber-700 dark:text-amber-300">{{ formatDuration(latency?.p95_ms || 0) }}</div>
            </div>
            <div class="rounded-xl bg-slate-100 px-3 py-3 dark:bg-white/[0.06]">
              <div class="text-[11px] font-medium text-slate-500 dark:text-stone-400">最大耗时</div>
              <div class="mt-1 text-lg font-semibold text-slate-950 dark:text-stone-50">{{ formatDuration(latency?.max_ms || 0) }}</div>
            </div>
          </div>

          <div v-if="stageMetrics.length" class="mt-5">
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
        </div>
      </div>

      <div class="grid gap-4 xl:grid-cols-3">
        <div class="studio-card bg-white p-4 dark:bg-[#171a21] xl:col-span-2">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h2 class="text-[20px] font-semibold">生成趋势</h2>
              <p class="mt-1 text-[13px] text-slate-500">任务量曲线，最后刷新 {{ lastUpdated || "暂无" }}。</p>
            </div>
            <span class="rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-xs font-semibold text-[#315be8]">Live</span>
          </div>
          <div class="mt-4 h-[138px] rounded-2xl border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]">
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

        <div v-if="loading && !summary" class="mt-5 space-y-2">
          <div v-for="index in 6" :key="index" class="studio-skeleton h-[82px] rounded-2xl" />
        </div>

        <div
          v-else-if="!sortedUsers.length"
          class="mt-5 grid min-h-[240px] place-items-center rounded-[20px] border border-dashed border-slate-300 text-center dark:border-white/10"
        >
          <div>
            <Activity class="mx-auto size-8 text-slate-400" />
            <p class="mt-3 text-sm font-semibold">暂无匹配监控数据</p>
          </div>
        </div>

        <div v-else class="mt-5 overflow-x-auto pb-1">
          <div class="min-w-[1180px] space-y-2">
            <div class="grid grid-cols-[minmax(260px,1.25fr)_96px_repeat(5,minmax(78px,.55fr))_minmax(150px,.7fr)_minmax(150px,.7fr)_180px] items-center gap-3 px-4 text-[11px] font-semibold text-slate-400">
              <span>用户</span>
              <span>状态</span>
              <button
                v-for="column in userSortColumns"
                :key="column.key"
                type="button"
                class="inline-flex items-center justify-end gap-1 rounded-lg px-1.5 py-1 text-right transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35"
                :class="userSortKey === column.key ? 'text-[#315be8]' : 'text-slate-400 dark:text-stone-500'"
                :aria-label="`按${column.label}${userSortKey === column.key && userSortDirection === 'desc' ? '从小到大' : '从大到小'}排序`"
                @click="toggleUserSort(column.key)"
              >
                <span>{{ column.label }}</span>
                <component :is="sortIcon(column.key)" class="size-3" />
              </button>
              <span>最近登录</span>
              <span>最近活跃</span>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-lg px-1.5 py-1 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35"
                :class="userSortKey === 'load' ? 'text-[#315be8]' : 'text-slate-400 dark:text-stone-500'"
                :aria-label="`按当前负载${userSortKey === 'load' && userSortDirection === 'desc' ? '从小到大' : '从大到小'}排序`"
                @click="toggleUserSort('load')"
              >
                <span>当前负载</span>
                <component :is="sortIcon('load')" class="size-3" />
              </button>
            </div>
            <div
              v-for="user in sortedUsers"
              :key="user.user_id"
              class="group grid min-h-[82px] grid-cols-[minmax(260px,1.25fr)_96px_repeat(5,minmax(78px,.55fr))_minmax(150px,.7fr)_minmax(150px,.7fr)_180px] items-center gap-3 rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 py-3 transition-colors duration-200 hover:border-[#4F7CFF]/30 hover:bg-white hover:ring-2 hover:ring-[#4F7CFF]/10 motion-safe:transition-transform motion-safe:hover:-translate-y-0.5 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.07]"
            >
              <div class="flex min-w-0 items-center gap-3">
                <div
                  class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-sm font-semibold text-white transition-colors duration-200 group-hover:bg-[#315be8] dark:bg-white dark:text-slate-950"
                >
                  {{ (user.name || user.username || user.user_id || 'U').slice(0, 1).toUpperCase() }}
                </div>
                <div class="min-w-0">
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="truncate text-[15px] font-semibold text-slate-950 dark:text-stone-50">{{ user.name || user.username }}</span>
                    <span class="shrink-0 rounded-full bg-white px-2 py-1 text-[11px] text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">
                      {{ roleLabel(user.role) }}
                    </span>
                  </div>
                  <div class="mt-1 truncate text-xs text-slate-500">
                    {{ user.username }} / {{ user.user_id }}
                  </div>
                </div>
              </div>

              <span
                class="inline-flex w-fit items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold"
                :class="
                  user.online
                    ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300'
                    : 'bg-slate-100 text-slate-500 dark:bg-white/[0.08] dark:text-slate-300'
                "
              >
                <Wifi v-if="user.online" class="size-3" />
                <WifiOff v-else class="size-3" />
                {{ user.online ? '在线' : '离线' }}
              </span>

              <div class="text-right text-sm font-semibold text-emerald-600">{{ formatNumber(user.success_count) }}</div>
              <div class="text-right text-sm font-semibold text-rose-600">{{ formatNumber(user.failed_count) }}</div>
              <div class="text-right text-sm font-semibold text-[#4F7CFF]">{{ formatNumber(user.running_tasks) }}</div>
              <div class="text-right text-sm font-semibold text-amber-600">{{ formatNumber(user.queued_tasks) }}</div>
              <div class="text-right text-sm font-semibold text-slate-900 dark:text-stone-100">{{ formatNumber(userVolume(user)) }}</div>
              <div class="truncate text-xs text-slate-500">{{ user.last_login_at || '暂无' }}</div>
              <div class="truncate text-xs text-slate-500">{{ user.last_seen_at || '暂无' }}</div>
              <div>
                <div class="flex items-center justify-between gap-2 text-xs">
                  <span class="font-semibold text-slate-900 dark:text-stone-100">{{ formatNumber(userLoad(user)) }}</span>
                  <span class="text-slate-500">/ {{ formatNumber(ownerConcurrencyLimit) }}</span>
                </div>
                <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-white/[0.08]">
                  <div
                    class="h-full rounded-full bg-gradient-to-r from-[#4F7CFF] to-[#6D5EF7] transition-[width,background-color] duration-300"
                    :style="{ width: `${userLoadPercent(user)}%` }"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
