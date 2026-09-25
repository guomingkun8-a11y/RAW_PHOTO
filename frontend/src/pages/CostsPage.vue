<script setup lang="ts">
import {
  AudioLines,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Download,
  Images,
  Layers3,
  MessageSquareText,
  RefreshCw,
  ReceiptText,
  Search,
  Video,
} from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import AnimatedNumber from "@/components/AnimatedNumber.vue";
import { useVisibilityAwareInterval } from "@/composables/useVisibilityAwareInterval";
import UpstreamBillingPanel from "@/features/billing/UpstreamBillingPanel.vue";
import {
  fetchMonitoringSummary,
  fetchMonitoringTasks,
  type MonitoringModelStat,
  type MonitoringSummary,
  type MonitoringTaskDetail,
  type MonitoringTaskDetails,
  type MonitoringSource,
  type MonitoringUserStat,
  type UpstreamBillingSource,
} from "@/lib/api";
import { storageKey } from "@/lib/storage-namespace";

type RangePreset = "today" | "7d" | "30d" | "all" | "custom";
type UpstreamBillingPanelApi = { refresh: (silent?: boolean) => Promise<void> };

const route = useRoute();
const router = useRouter();
const summary = ref<MonitoringSummary | null>(null);
const details = ref<MonitoringTaskDetails | null>(null);
const loading = ref(true);
const refreshing = ref(false);
const loadError = ref("");
const lastUpdated = ref("");
const autoRefresh = ref(true);
const taskQuery = ref("");
const costRecordPage = ref(1);
const costRecordCursors = ref<Array<MonitoringTaskDetails["next_cursor"]>>([null]);
const upstreamBillingPanel = ref<UpstreamBillingPanelApi | null>(null);
const upstreamLoading = ref(false);
let requestSequence = 0;
let taskQueryTimer = 0;
const COST_RECORD_PAGE_SIZE = 20;
const COSTS_AUTO_REFRESH_STORAGE_KEY = storageKey("raw-costs-auto-refresh");
const { restart: restartAutoRefresh } = useVisibilityAwareInterval(() => load(true), 15_000);

const rangePreset = ref<RangePreset>("today");
const costSource = ref<UpstreamBillingSource>("all");
const customStartDate = ref("");
const customEndDate = ref("");
const appliedRange = ref<{ startAt?: string; endAt?: string }>({});
const appliedRangeLabel = ref("今天");

const rangePresets: Array<{ value: RangePreset; label: string }> = [
  { value: "today", label: "今天" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "all", label: "全部" },
  { value: "custom", label: "自定义" },
];

const sourceOptions = [
  { value: "all" as const, label: "全部账单", icon: Layers3 },
  { value: "image" as const, label: "生图费用", icon: Images },
  { value: "video" as const, label: "生视频费用", icon: Video },
  { value: "chat" as const, label: "对话费用", icon: MessageSquareText },
  { value: "audio" as const, label: "音频费用", icon: AudioLines },
];

const isUpstreamCostSource = computed(() => ["all", "chat", "audio"].includes(costSource.value));
const pageBusy = computed(() => isUpstreamCostSource.value
  ? upstreamLoading.value
  : loading.value || refreshing.value);
const upstreamSyncDays = computed(() => {
  if (rangePreset.value === "today") return 1;
  if (rangePreset.value === "7d") return 7;
  if (rangePreset.value === "30d" || rangePreset.value === "all") return 30;
  const start = new Date(`${customStartDate.value}T00:00:00`);
  const end = new Date(`${customEndDate.value}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 2;
  return Math.min(30, Math.max(1, Math.ceil((end.getTime() - start.getTime()) / 86_400_000) + 1));
});
const upstreamPanelKey = computed(() => [
  costSource.value,
  appliedRange.value.startAt || "",
  appliedRange.value.endAt || "",
].join(":"));

const numberFormat = new Intl.NumberFormat("zh-CN");
const costFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 });

function formatNumber(value: number) {
  return numberFormat.format(value || 0);
}

function numericCost(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

function hasCost(value: unknown) {
  if (value === null || value === undefined || value === "") return false;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= 0;
}

function formatCost(value: unknown, fallback = "0") {
  return hasCost(value) ? `￥${costFormat.format(numericCost(value))}` : fallback === "0" ? "￥0" : fallback;
}

function formatPercent(value: number) {
  return `${value.toFixed(Number.isInteger(value) ? 0 : 1)}%`;
}

function startOfToday() {
  const value = new Date();
  value.setHours(0, 0, 0, 0);
  return value;
}

function addDays(value: Date, days: number) {
  const result = new Date(value);
  result.setDate(result.getDate() + days);
  return result;
}

function toLocalDateTimeValue(value: Date) {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function toLocalDateValue(value: Date) {
  return toLocalDateTimeValue(value).slice(0, 10);
}

function formatRangeValue(value: string) {
  if (!value) return "";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function isValidRangePreset(value: unknown): value is RangePreset {
  return ["today", "7d", "30d", "all", "custom"].includes(String(value));
}

function isValidCostSource(value: unknown): value is UpstreamBillingSource {
  return ["all", "image", "video", "chat", "audio"].includes(String(value));
}

function restoreRangeFromUrl() {
  costSource.value = isValidCostSource(route.query.source) ? route.query.source : "all";
  const preset = String(route.query.range || "today");
  rangePreset.value = isValidRangePreset(preset) ? preset : "today";
  if (rangePreset.value !== "custom") return;
  customStartDate.value = String(route.query.start || toLocalDateValue(startOfToday()));
  customEndDate.value = String(route.query.end || toLocalDateValue(startOfToday()));
}

function syncRangeUrl() {
  const nextQuery = { ...route.query, range: rangePreset.value, source: costSource.value } as Record<string, string | undefined>;
  if (rangePreset.value === "custom") {
    nextQuery.start = customStartDate.value;
    nextQuery.end = customEndDate.value;
  } else {
    delete nextQuery.start;
    delete nextQuery.end;
  }
  void router.replace({ query: nextQuery });
}

function sourceTitle() {
  return {
    all: "全部模型",
    image: "生图",
    video: "生视频",
    chat: "对话",
    audio: "音频",
  }[costSource.value];
}

function selectCostSource(value: UpstreamBillingSource) {
  if (costSource.value === value) return;
  costSource.value = value;
  taskQuery.value = "";
  resetCostRecordPagination();
  syncRangeUrl();
  void load(false);
}

function rangeForSelection() {
  if (rangePreset.value === "all") return {};
  if (rangePreset.value === "custom") {
    const endDate = new Date(`${customEndDate.value}T00:00:00`);
    return {
      startAt: `${customStartDate.value}T00:00`,
      endAt: toLocalDateTimeValue(addDays(endDate, 1)),
    };
  }
  const today = startOfToday();
  const days = rangePreset.value === "7d" ? 7 : rangePreset.value === "30d" ? 30 : 1;
  return {
    startAt: toLocalDateTimeValue(addDays(today, 1 - days)),
    endAt: toLocalDateTimeValue(addDays(today, 1)),
  };
}

function commitRangeSelection() {
  appliedRange.value = rangeForSelection();
  if (rangePreset.value === "all") appliedRangeLabel.value = "全部历史";
  else if (rangePreset.value === "today") appliedRangeLabel.value = "今天";
  else if (rangePreset.value === "7d") appliedRangeLabel.value = "最近 7 个自然日";
  else if (rangePreset.value === "30d") appliedRangeLabel.value = "最近 30 个自然日";
  else appliedRangeLabel.value = `${formatRangeValue(customStartDate.value)} 至 ${formatRangeValue(customEndDate.value)}`;
}

function selectedRange() {
  return appliedRange.value;
}

function resetCostRecordPagination() {
  costRecordPage.value = 1;
  costRecordCursors.value = [null];
}

async function load(silent = false) {
  if (isUpstreamCostSource.value) {
    await nextTick();
    await upstreamBillingPanel.value?.refresh(silent);
    return;
  }
  const sequence = ++requestSequence;
  const requestedPage = costRecordPage.value;
  const cursor = requestedPage === 1 ? null : costRecordCursors.value[requestedPage - 1];
  if (cursor === undefined) {
    resetCostRecordPagination();
    return load(silent);
  }
  silent ? (refreshing.value = true) : (loading.value = true);
  try {
    const range = selectedRange();
    const [nextSummary, nextDetails] = await Promise.all([
      fetchMonitoringSummary({ ...range, source: costSource.value as MonitoringSource }),
      fetchMonitoringTasks({
        ...range,
        source: costSource.value as MonitoringSource,
        status: costSource.value === "video" ? "all" : "success",
        limit: COST_RECORD_PAGE_SIZE,
        cursor,
        q: taskQuery.value,
        costOnly: true,
      }),
    ]);
    if (sequence !== requestSequence) return;
    summary.value = nextSummary;
    details.value = nextDetails;
    costRecordCursors.value = costRecordCursors.value.slice(0, requestedPage);
    if (nextDetails.next_cursor) costRecordCursors.value[requestedPage] = nextDetails.next_cursor;
    loadError.value = "";
    lastUpdated.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch (error) {
    if (sequence !== requestSequence) return;
    loadError.value = error instanceof Error ? error.message : "读取费用记录失败";
    toast.error(loadError.value);
  } finally {
    if (sequence !== requestSequence) return;
    loading.value = false;
    refreshing.value = false;
  }
}

function selectRangePreset(value: RangePreset) {
  rangePreset.value = value;
  resetCostRecordPagination();
  if (value === "custom") {
    if (!customStartDate.value) customStartDate.value = toLocalDateValue(startOfToday());
    if (!customEndDate.value) customEndDate.value = toLocalDateValue(startOfToday());
    return;
  }
  commitRangeSelection();
  syncRangeUrl();
  void load(true);
}

function applyCustomRange() {
  if (!customStartDate.value || !customEndDate.value) {
    toast.error("请选择开始和结束日期");
    return;
  }
  if (customStartDate.value > customEndDate.value) {
    toast.error("结束日期不能早于开始日期");
    return;
  }
  resetCostRecordPagination();
  commitRangeSelection();
  syncRangeUrl();
  void load(true);
}

function scheduleTaskQueryLoad() {
  window.clearTimeout(taskQueryTimer);
  taskQueryTimer = window.setTimeout(() => {
    resetCostRecordPagination();
    void load(true);
  }, 300);
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value;
  try {
    window.localStorage.setItem(COSTS_AUTO_REFRESH_STORAGE_KEY, String(autoRefresh.value));
  } catch {
    // Local storage can be unavailable in private or embedded browser contexts.
  }
  restartAutoRefresh(autoRefresh.value);
}

function modelCostShare(item: MonitoringModelStat) {
  if (!totalCost.value) return 0;
  return Math.round((numericCost(item.cost_total) / totalCost.value) * 1000) / 10;
}

function ownerName(ownerId: string) {
  const owner = summary.value?.users.find((item) => item.user_id === ownerId);
  return owner?.name || owner?.username || ownerId;
}

function csvCell(value: unknown) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportCostsCsv() {
  const rows = [
    ["模型", "计费次数", "总费用", "平均费用", "费用占比"],
    ...modelCosts.value.map((item) => [
      item.model,
      item.cost_count,
      formatCost(item.cost_total),
      formatCost(item.cost_average),
      `${modelCostShare(item)}%`,
    ]),
    [],
    ["用户", "用户 ID", "计费次数", "总费用", "平均费用"],
    ...userCosts.value.map((item) => [
      item.name || item.username,
      item.user_id,
      item.cost_count || 0,
      formatCost(item.cost_total),
      formatCost(item.cost_average),
    ]),
    [],
    ["来源", "用户", "用户 ID", "模型", "费用", "完成时间", "上游 task_id", "任务 task_id"],
    ...filteredCostItems.value.map((item) => [
      sourceLabel(item),
      ownerName(item.owner_id),
      item.owner_id,
      item.model,
      formatCost(item.cost),
      item.completed_at,
      upstreamLabel(item),
      item.task_id,
    ]),
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `raw-costs-${rangePreset.value}-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast.success(`已导出 ${filteredCostItems.value.length} 条费用记录`);
}

const totalCost = computed(() => numericCost(summary.value?.total_cost));
const totalCostCount = computed(() => summary.value?.cost_count || 0);
const averageCost = computed(() => totalCostCount.value ? totalCost.value / totalCostCount.value : 0);
const modelCosts = computed<MonitoringModelStat[]>(() =>
  [...(summary.value?.models || [])]
    .filter((item) => Number(item.cost_count || 0) > 0)
    .sort((a, b) => numericCost(b.cost_total) - numericCost(a.cost_total)),
);
const userCosts = computed<MonitoringUserStat[]>(() =>
  [...(summary.value?.users || [])]
    .filter((item) => Number(item.cost_count || 0) > 0)
    .sort((a, b) => numericCost(b.cost_total) - numericCost(a.cost_total)),
);
const costItems = computed<MonitoringTaskDetail[]>(() => details.value?.items || []);
const filteredCostItems = computed(() => costItems.value.filter((item) => hasCost(item.cost)));
const costRecordTotalPages = computed(() => Math.max(1, Math.ceil((details.value?.record_count || 0) / COST_RECORD_PAGE_SIZE)));
const costRecordPageStart = computed(() => filteredCostItems.value.length ? (costRecordPage.value - 1) * COST_RECORD_PAGE_SIZE + 1 : 0);
const costRecordPageEnd = computed(() => (costRecordPage.value - 1) * COST_RECORD_PAGE_SIZE + filteredCostItems.value.length);
const paginatedCostItems = computed(() => filteredCostItems.value);

function goToCostRecordPage(page: number) {
  const nextPage = Math.min(costRecordTotalPages.value, Math.max(1, page));
  if (nextPage === costRecordPage.value || loading.value || refreshing.value) return;
  costRecordPage.value = nextPage;
  void load(true);
}

function sourceLabel(item: MonitoringTaskDetail) {
  if (item.source_type === "video") return "生视频";
  return item.source_type === "event" ? "生图记录" : "生图";
}

function upstreamLabel(item: MonitoringTaskDetail) {
  return item.upstream_task_id || "无";
}

onMounted(() => {
  restoreRangeFromUrl();
  if (rangePreset.value === "custom" && (!customStartDate.value || !customEndDate.value)) {
    customStartDate.value = toLocalDateValue(startOfToday());
    customEndDate.value = toLocalDateValue(startOfToday());
  }
  commitRangeSelection();
  try {
    autoRefresh.value = window.localStorage.getItem(COSTS_AUTO_REFRESH_STORAGE_KEY) !== "false";
  } catch {
    autoRefresh.value = true;
  }
  void load();
  restartAutoRefresh(autoRefresh.value);
});

watch(taskQuery, () => {
  scheduleTaskQueryLoad();
});

watch(costRecordTotalPages, (totalPages) => {
  if (costRecordPage.value > totalPages) {
    resetCostRecordPagination();
    void load(true);
  }
});

onBeforeUnmount(() => window.clearTimeout(taskQueryTimer));

</script>

<template>
  <section class="costs-page min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <header class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div class="inline-flex items-center gap-2 rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
              <ReceiptText class="size-3.5" />
              费用管理
            </div>
            <h1 class="mt-2 text-[30px] font-semibold text-slate-950 dark:text-stone-50">费用记录</h1>
            <p class="mt-1 max-w-3xl text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              <template v-if="isUpstreamCostSource">
                查看 {{ appliedRangeLabel }} 内 {{ sourceTitle() }} 的上游实际扣费、失败与退款记录。
              </template>
              <template v-else>
                查看 {{ appliedRangeLabel }} 内所有已完成{{ sourceTitle() }}任务的本地费用记录。
              </template>
            </p>
            <div class="mt-3 inline-flex max-w-full overflow-x-auto rounded-xl bg-[#F1F5F9] p-1 dark:bg-white/[0.06]" aria-label="费用来源">
              <button
                v-for="option in sourceOptions"
                :key="option.value"
                type="button"
                class="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-[13px] font-medium text-slate-500 transition-colors"
                :class="costSource === option.value ? 'bg-white text-[#315be8] shadow-sm dark:bg-white/10 dark:text-white' : 'hover:text-slate-900 dark:hover:text-white'"
                :aria-pressed="costSource === option.value"
                @click="selectCostSource(option.value)"
              >
                <component :is="option.icon" class="size-4" />
                {{ option.label }}
              </button>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <span class="text-xs text-slate-500">更新于 {{ lastUpdated || "--" }}</span>
            <label class="inline-flex h-11 cursor-pointer items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300">
              <input class="peer sr-only" type="checkbox" :checked="autoRefresh" @change="toggleAutoRefresh" />
              <span class="relative h-5 w-9 rounded-full bg-slate-200 transition-colors peer-checked:bg-[#4F7CFF] dark:bg-white/15">
                <span class="absolute left-0.5 top-0.5 size-4 rounded-full bg-white transition-transform peer-checked:translate-x-4" />
              </span>
              自动刷新
            </label>
            <button
              type="button"
              class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]"
              :disabled="pageBusy"
              @click="load(true)"
            >
              <RefreshCw class="size-4" :class="pageBusy ? 'animate-spin' : ''" />
              刷新
            </button>
          </div>
        </div>

        <div class="mt-5 flex flex-col gap-3 border-t border-black/[0.06] pt-4 dark:border-white/10 2xl:flex-row 2xl:items-end 2xl:justify-between">
          <div>
            <div class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-stone-100">
              <CalendarDays class="size-4 text-[#4F7CFF]" />
              统计周期
            </div>
            <div class="mt-3 inline-flex max-w-full overflow-x-auto rounded-xl bg-[#F1F5F9] p-1 dark:bg-white/[0.06]">
              <button
                v-for="item in rangePresets"
                :key="item.value"
                type="button"
                class="h-9 shrink-0 rounded-lg px-3 text-[13px] font-medium text-slate-500 transition-colors"
                :class="rangePreset === item.value ? 'bg-white text-[#315be8] shadow-sm dark:bg-white/10 dark:text-white' : 'hover:text-slate-900 dark:hover:text-white'"
                :aria-pressed="rangePreset === item.value"
                @click="selectRangePreset(item.value)"
              >
                {{ item.label }}
              </button>
            </div>
          </div>

          <div v-if="rangePreset === 'custom'" class="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label class="text-xs font-medium text-slate-500">
              开始日期
              <input v-model="customStartDate" type="date" class="studio-input mt-1 block h-10 min-w-[170px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
            </label>
            <label class="text-xs font-medium text-slate-500">
              结束日期
              <input v-model="customEndDate" type="date" class="studio-input mt-1 block h-10 min-w-[170px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
            </label>
            <button type="button" class="studio-button h-10 min-w-[72px] bg-slate-950 px-4 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" @click="applyCustomRange">
              应用
            </button>
          </div>
        </div>
      </header>

      <UpstreamBillingPanel
        v-if="isUpstreamCostSource"
        :key="upstreamPanelKey"
        ref="upstreamBillingPanel"
        :source="costSource"
        :start-at="appliedRange.startAt"
        :end-at="appliedRange.endAt"
        :range-label="appliedRangeLabel"
        :sync-days="upstreamSyncDays"
        @updated="lastUpdated = $event"
        @loading="upstreamLoading = $event"
      />

      <div v-if="!isUpstreamCostSource && loadError" class="flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
        <span>加载费用记录失败：{{ loadError }}</span>
        <button type="button" class="studio-button inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white hover:bg-rose-700" @click="load(true)">
          <RefreshCw class="size-3.5" />
          重试
        </button>
      </div>

      <div v-if="!isUpstreamCostSource && loading && !summary" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div v-for="index in 4" :key="index" class="studio-skeleton h-28 rounded-2xl" />
      </div>

      <template v-else-if="!isUpstreamCostSource">
        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
            <div class="text-xs font-medium text-slate-500">总费用</div>
            <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
              <AnimatedNumber :value="totalCost" :formatter="formatCost" />
            </div>
            <div class="mt-1 text-[11px] text-slate-400">
              <AnimatedNumber :value="totalCostCount" :formatter="formatNumber" /> 次计费
            </div>
          </div>
          <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
            <div class="text-xs font-medium text-slate-500">平均费用</div>
            <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
              <AnimatedNumber :value="averageCost" :formatter="formatCost" />
            </div>
            <div class="mt-1 text-[11px] text-slate-400">每次任务的平均费用</div>
          </div>
          <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
            <div class="text-xs font-medium text-slate-500">计费模型</div>
            <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
              <AnimatedNumber :value="modelCosts.length" :formatter="formatNumber" />
            </div>
            <div class="mt-1 text-[11px] text-slate-400">有费用记录的模型</div>
          </div>
          <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
            <div class="text-xs font-medium text-slate-500">付费用户</div>
            <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
              <AnimatedNumber :value="userCosts.length" :formatter="formatNumber" />
            </div>
            <div class="mt-1 text-[11px] text-slate-400">产生费用的用户</div>
          </div>
        </div>

        <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.08fr)_minmax(0,0.92fr)]">
          <section class="studio-card overflow-hidden bg-white p-5 dark:bg-[#171a21]">
            <div class="flex items-end justify-between gap-4">
              <div>
                <h2 class="text-[20px] font-semibold">模型费用</h2>
                <p class="mt-1 text-[13px] text-slate-500">按模型汇总实际费用。</p>
              </div>
              <span class="text-xs text-slate-500">{{ formatNumber(modelCosts.length) }} 个模型</span>
            </div>
            <div v-if="modelCosts.length" class="mt-4 overflow-hidden rounded-xl border border-black/[0.06] dark:border-white/10">
              <table class="w-full table-fixed border-collapse text-[13px]">
                <colgroup>
                  <col class="w-[34%]" />
                  <col class="w-[13%]" />
                  <col class="w-[19%]" />
                  <col class="w-[19%]" />
                  <col class="w-[15%]" />
                </colgroup>
                <thead>
                  <tr class="border-b border-black/[0.06] bg-slate-50/70 text-left text-[11px] font-semibold text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-stone-400">
                    <th class="px-4 py-2.5 font-semibold">模型</th>
                    <th class="px-2 py-2.5 text-right font-semibold">次数</th>
                    <th class="px-2 py-2.5 text-right font-semibold">总费用</th>
                    <th class="px-2 py-2.5 text-right font-semibold">平均费用</th>
                    <th class="px-4 py-2.5 text-right font-semibold">占比</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in modelCosts" :key="item.model" class="group border-b border-black/[0.06] transition-colors last:border-0 hover:bg-[#4F7CFF]/[0.035] dark:border-white/10 dark:hover:bg-white/[0.04]">
                    <td class="min-w-0 px-4 py-3.5">
                      <div class="truncate font-mono text-[13px] font-semibold text-slate-900 dark:text-stone-100" :title="item.model">{{ item.model }}</div>
                    </td>
                    <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">
                      <AnimatedNumber :value="item.cost_count" :formatter="formatNumber" />
                    </td>
                    <td class="px-2 py-3.5 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                      <AnimatedNumber :value="item.cost_total" :formatter="formatCost" />
                    </td>
                    <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">
                      <AnimatedNumber :value="item.cost_average" :formatter="formatCost" />
                    </td>
                    <td class="px-4 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">
                      <div class="ml-auto flex max-w-[76px] flex-col items-end gap-1.5">
                        <span><AnimatedNumber :value="modelCostShare(item)" :formatter="formatPercent" /></span>
                        <span class="block h-1 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-white/10" aria-hidden="true">
                          <span class="block h-full rounded-full bg-[#4F7CFF]" :style="{ width: `${Math.min(100, modelCostShare(item))}%` }" />
                        </span>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="mt-4 rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500 dark:border-white/10">
              当前筛选条件下暂无模型费用数据
            </div>
          </section>

          <section class="studio-card overflow-hidden bg-white p-5 dark:bg-[#171a21]">
            <div class="flex items-end justify-between gap-4">
              <div>
                <h2 class="text-[20px] font-semibold">用户费用</h2>
                <p class="mt-1 text-[13px] text-slate-500">按用户汇总实际费用。</p>
              </div>
              <span class="text-xs text-slate-500">{{ formatNumber(userCosts.length) }} 位用户</span>
            </div>
            <div v-if="userCosts.length" class="mt-4 overflow-hidden rounded-xl border border-black/[0.06] dark:border-white/10">
              <table class="w-full table-fixed border-collapse text-[13px]">
                <colgroup>
                  <col class="w-[42%]" />
                  <col class="w-[14%]" />
                  <col class="w-[22%]" />
                  <col class="w-[22%]" />
                </colgroup>
                <thead>
                  <tr class="border-b border-black/[0.06] bg-slate-50/70 text-left text-[11px] font-semibold text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-stone-400">
                    <th class="px-4 py-2.5 font-semibold">用户</th>
                    <th class="px-2 py-2.5 text-right font-semibold">次数</th>
                    <th class="px-2 py-2.5 text-right font-semibold">总费用</th>
                    <th class="px-4 py-2.5 text-right font-semibold">平均费用</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in userCosts" :key="item.user_id" class="group border-b border-black/[0.06] transition-colors last:border-0 hover:bg-[#4F7CFF]/[0.035] dark:border-white/10 dark:hover:bg-white/[0.04]">
                    <td class="min-w-0 px-4 py-3.5">
                      <div class="truncate font-semibold text-slate-900 dark:text-stone-100" :title="item.name || item.username || item.user_id">{{ item.name || item.username || item.user_id }}</div>
                      <div class="mt-0.5 truncate font-mono text-[11px] text-slate-400" :title="item.user_id">{{ item.user_id }}</div>
                    </td>
                    <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">
                      <AnimatedNumber :value="item.cost_count || 0" :formatter="formatNumber" />
                    </td>
                    <td class="px-2 py-3.5 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                      <AnimatedNumber :value="item.cost_total" :formatter="formatCost" />
                    </td>
                    <td class="px-4 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">
                      <AnimatedNumber :value="item.cost_average" :formatter="formatCost" />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="mt-4 rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500 dark:border-white/10">
              当前筛选条件下暂无用户费用数据
            </div>
          </section>
        </div>

        <section class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <h2 class="text-[20px] font-semibold">费用明细</h2>
              <p class="mt-1 text-[13px] text-slate-500">每一笔已完成{{ sourceTitle() }}任务返回的实际费用。</p>
            </div>
            <div class="flex w-full flex-col gap-2 sm:flex-row xl:w-auto">
              <div class="relative w-full sm:w-[320px]">
                <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <input v-model="taskQuery" class="studio-input h-11 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索用户、模型或 task_id" aria-label="搜索用户、模型或 task_id" />
              </div>
              <button type="button" class="studio-button inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-black/[0.06] px-3 text-sm font-medium dark:border-white/10" :disabled="!filteredCostItems.length" @click="exportCostsCsv">
                <Download class="size-4" />
                导出 CSV
              </button>
            </div>
          </div>

          <div v-if="filteredCostItems.length" class="mt-4 overflow-x-auto">
            <table class="w-full min-w-[1200px] border-collapse text-sm">
              <thead>
                <tr class="border-b border-black/[0.06] text-left text-[11px] font-semibold text-slate-400 dark:border-white/10">
                  <th class="px-3 py-2 font-semibold">完成时间</th>
                  <th class="px-3 py-2 font-semibold">来源</th>
                  <th class="px-3 py-2 font-semibold">用户</th>
                  <th class="px-3 py-2 font-semibold">模型</th>
                  <th class="px-3 py-2 text-right font-semibold">费用</th>
                  <th class="px-3 py-2 font-semibold">上游 task_id</th>
                  <th class="px-3 py-2 font-semibold">task_id</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(item, index) in paginatedCostItems"
                  :key="item.row_key"
                  class="studio-row-enter border-b border-black/[0.06] last:border-0 dark:border-white/10"
                  :style="{ '--studio-row-index': index }"
                >
                  <td class="whitespace-nowrap px-3 py-3 text-slate-600 dark:text-stone-300">{{ item.completed_at || "未知时间" }}</td>
                  <td class="px-3 py-3">
                    <span class="rounded-md px-2 py-1 text-[11px] font-semibold" :class="item.source_type === 'event' ? 'bg-[#4F7CFF]/10 text-[#315be8]' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300'">
                      {{ sourceLabel(item) }}
                    </span>
                  </td>
                  <td class="px-3 py-3">
                    <div class="font-medium text-slate-900 dark:text-stone-100">{{ ownerName(item.owner_id) }}</div>
                    <div class="font-mono text-[11px] text-slate-400">{{ item.owner_id }}</div>
                  </td>
                  <td class="px-3 py-3 font-mono text-[13px] text-slate-700 dark:text-stone-200">{{ item.model || "unknown" }}</td>
                  <td class="px-3 py-3 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                    <AnimatedNumber :value="item.cost" :formatter="formatCost" />
                  </td>
                  <td class="max-w-[220px] truncate px-3 py-3 font-mono text-[11px] text-slate-500" :title="upstreamLabel(item)">{{ upstreamLabel(item) }}</td>
                  <td class="max-w-[220px] truncate px-3 py-3 font-mono text-[11px] text-slate-500" :title="item.task_id">{{ item.task_id }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="filteredCostItems.length" class="mt-4 flex flex-col gap-3 border-t border-black/[0.06] pt-4 text-sm text-slate-500 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
            <span>
              显示 {{ formatNumber(costRecordPageStart) }}-{{ formatNumber(costRecordPageEnd) }} / {{ formatNumber(details?.record_count || 0) }} 条记录，每页 20 条
            </span>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
                :disabled="costRecordPage <= 1"
                aria-label="上一页"
                @click="goToCostRecordPage(costRecordPage - 1)"
              >
                <ChevronLeft class="size-4" />
              </button>
              <span class="min-w-20 text-center text-sm font-semibold tabular-nums text-slate-900 dark:text-stone-100">
                {{ costRecordPage }} / {{ costRecordTotalPages }}
              </span>
              <button
                type="button"
                class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
                :disabled="costRecordPage >= costRecordTotalPages"
                aria-label="下一页"
                @click="goToCostRecordPage(costRecordPage + 1)"
              >
                <ChevronRight class="size-4" />
              </button>
            </div>
          </div>
          <div v-else class="mt-4 rounded-xl border border-dashed border-slate-300 px-4 py-10 text-center text-sm text-slate-500 dark:border-white/10">
            {{ costItems.length ? "没有匹配的费用记录" : "当前筛选条件下暂无费用记录" }}
          </div>
        </section>
      </template>
    </div>
  </section>
</template>
