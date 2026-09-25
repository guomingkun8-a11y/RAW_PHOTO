<script setup lang="ts">
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
} from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { toast } from "vue-sonner";

import AnimatedNumber from "@/components/AnimatedNumber.vue";
import { formatImageModel } from "@/lib/image-models";
import {
  fetchUpstreamBillingRecords,
  fetchUpstreamBillingSummary,
  syncUpstreamBilling,
  type UpstreamBillingModelStat,
  type UpstreamBillingRecords,
  type UpstreamBillingSource,
  type UpstreamBillingSummary,
} from "@/lib/api";

const props = withDefaults(defineProps<{
  source: UpstreamBillingSource;
  startAt?: string;
  endAt?: string;
  rangeLabel: string;
  syncDays: number;
}>(), {
  startAt: "",
  endAt: "",
});

const emit = defineEmits<{
  updated: [value: string];
  loading: [value: boolean];
}>();

const summary = ref<UpstreamBillingSummary | null>(null);
const details = ref<UpstreamBillingRecords | null>(null);
const loading = ref(true);
const refreshing = ref(false);
const syncing = ref(false);
const loadError = ref("");
const query = ref("");
const page = ref(1);
let requestSequence = 0;
let queryTimer = 0;
const PAGE_SIZE = 20;

const numberFormat = new Intl.NumberFormat("zh-CN");
const costFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 });

const busy = computed(() => loading.value || refreshing.value || syncing.value);
const modelCosts = computed(() => [...(summary.value?.models || [])].sort((a, b) => b.cost - a.cost));
const userCosts = computed(() => [...(summary.value?.users || [])].sort((a, b) => b.cost - a.cost));
const records = computed(() => details.value?.items || []);
const totalPages = computed(() => Math.max(1, Math.ceil((details.value?.record_count || 0) / PAGE_SIZE)));
const pageStart = computed(() => records.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0);
const pageEnd = computed(() => (page.value - 1) * PAGE_SIZE + records.value.length);
const syncStatus = computed(() => summary.value?.sync);

watch(busy, (value) => emit("loading", value), { immediate: true });

function numeric(value: unknown) {
  const result = typeof value === "number" ? value : Number(value);
  return Number.isFinite(result) && result >= 0 ? result : 0;
}

function formatNumber(value: number) {
  return numberFormat.format(value || 0);
}

function isMoneyUnit(unit: string) {
  return ["CNY", "RMB", "人民币", "元", "算力"].includes(unit);
}

function displayUnit(unit: string) {
  return unit === "算力" ? "￥" : unit;
}

function formatCost(value: unknown, explicitUnit = "") {
  const formatted = costFormat.format(numeric(value));
  const unit = explicitUnit || summary.value?.unit || details.value?.unit || "";
  if (isMoneyUnit(unit)) return `￥${formatted}`;
  if (!unit || unit === "mixed") return formatted;
  return `${formatted} ${displayUnit(unit)}`;
}

function formatCostNumber(value: unknown, explicitUnit = "") {
  const unit = explicitUnit || summary.value?.unit || details.value?.unit || "";
  const formatted = costFormat.format(numeric(value));
  return isMoneyUnit(unit) ? `￥${formatted}` : formatted;
}

function displayModel(model: string, requestedModel = "", modelVersion = "") {
  const source = requestedModel || model;
  if (!requestedModel && modelVersion && /image-2\.5/i.test(source)) {
    return `gpt-image-2.5 · ${modelVersion}`;
  }
  if (!requestedModel && /^(?:tt|gpt)-image-2\.5$/i.test(source)) {
    return "gpt-image-2.5（未区分版本）";
  }
  return formatImageModel(source);
}

function formatDateTime(value: string) {
  if (!value) return "--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.replace("T", " ");
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function typeLabel(value: string) {
  return {
    image: "生图",
    video: "视频",
    chat: "对话",
    audio: "音频",
  }[value] || value || "其他";
}

function typeClass(value: string) {
  if (value === "video") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300";
  if (value === "chat") return "bg-violet-50 text-violet-700 dark:bg-violet-400/10 dark:text-violet-300";
  if (value === "audio") return "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300";
  return "bg-blue-50 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300";
}

function stateLabel(value: string) {
  if (["success", "succeeded", "completed", "complete"].includes(value)) return "成功";
  if (["failed", "error"].includes(value)) return "失败";
  return value || "未知";
}

function stateClass(value: string) {
  return ["success", "succeeded", "completed", "complete"].includes(value)
    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300"
    : "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
}

function modelCostShare(item: UpstreamBillingModelStat) {
  if (!summary.value?.total_cost) return 0;
  return Math.round((numeric(item.cost) / summary.value.total_cost) * 1000) / 10;
}

function syncStateLabel() {
  const status = syncStatus.value;
  if (!status?.enabled) return "费用同步未启用";
  if (!status.configured) return "费用同步缺少 API Key";
  if (status.status === "running") return "正在同步上游账单";
  if (status.status === "error" || status.status === "unavailable") return "最近一次同步失败";
  if (status.last_success_at) return "上游账单已同步";
  return "等待首次同步";
}

function syncStateClass() {
  const status = syncStatus.value;
  if (status?.status === "error" || status?.status === "unavailable" || !status?.configured) {
    return "text-rose-700 dark:text-rose-300";
  }
  if (status?.status === "running") return "text-[#315be8] dark:text-blue-300";
  return "text-emerald-700 dark:text-emerald-300";
}

async function refresh(silent = false) {
  const sequence = ++requestSequence;
  silent ? (refreshing.value = true) : (loading.value = true);
  try {
    const options = {
      source: props.source,
      startAt: props.startAt,
      endAt: props.endAt,
    };
    const [nextSummary, nextDetails] = await Promise.all([
      fetchUpstreamBillingSummary(options),
      fetchUpstreamBillingRecords({
        ...options,
        q: query.value,
        limit: PAGE_SIZE,
        offset: (page.value - 1) * PAGE_SIZE,
      }),
    ]);
    if (sequence !== requestSequence) return;
    summary.value = nextSummary;
    details.value = nextDetails;
    loadError.value = "";
    emit("updated", new Date().toLocaleTimeString("zh-CN", { hour12: false }));
  } catch (error) {
    if (sequence !== requestSequence) return;
    loadError.value = error instanceof Error ? error.message : "读取上游费用账单失败";
    if (!silent) toast.error(loadError.value);
  } finally {
    if (sequence !== requestSequence) return;
    loading.value = false;
    refreshing.value = false;
  }
}

async function runSync() {
  if (syncing.value || !syncStatus.value?.enabled || !syncStatus.value?.configured) return;
  syncing.value = true;
  try {
    const result = await syncUpstreamBilling(props.syncDays);
    toast.success(`已同步 ${formatNumber(result.records_upserted)} 条上游费用记录`);
    page.value = 1;
    await refresh(true);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "同步上游费用失败");
  } finally {
    syncing.value = false;
  }
}

function scheduleQuery() {
  window.clearTimeout(queryTimer);
  queryTimer = window.setTimeout(() => {
    page.value = 1;
    void refresh(true);
  }, 300);
}

function goToPage(value: number) {
  const nextPage = Math.min(totalPages.value, Math.max(1, value));
  if (nextPage === page.value || busy.value) return;
  page.value = nextPage;
  void refresh(true);
}

function csvCell(value: unknown) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportCsv() {
  const rows = [
    ["模型", "类型", "调用次数", "成功", "失败", "实际费用", "退款次数", "退款额"],
    ...modelCosts.value.map((item) => [
      displayModel(item.model, "", item.model_version),
      typeLabel(item.model_type),
      item.count,
      item.success_count,
      item.failed_count,
      formatCost(item.cost),
      item.refunded_count,
      formatCost(item.refunded_amount),
    ]),
    [],
    ["用户", "用户 ID", "调用次数", "实际费用", "退款次数", "退款额"],
    ...userCosts.value.map((item) => [
      item.name || item.username || "未归属",
      item.owner_id,
      item.count,
      formatCost(item.cost),
      item.refunded_count,
      formatCost(item.refunded_amount),
    ]),
    [],
    ["完成时间", "类型", "状态", "用户", "模型", "实际费用", "退款额", "渠道", "上游 task_id", "本地 task_id"],
    ...records.value.map((item) => [
      item.completed_at || item.event_at,
      typeLabel(item.model_type),
      stateLabel(item.state),
      item.owner_name,
      displayModel(item.model, item.requested_model, item.model_version),
      formatCost(item.cost, item.unit),
      formatCost(item.refunded_amount, item.unit),
      item.channel_group,
      item.upstream_task_id,
      item.local_task_id,
    ]),
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `upstream-costs-${props.source}-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast.success(`已导出当前页 ${records.value.length} 条费用明细`);
}

watch(query, scheduleQuery);
watch(totalPages, (value) => {
  if (page.value > value) {
    page.value = 1;
    void refresh(true);
  }
});
onBeforeUnmount(() => window.clearTimeout(queryTimer));

defineExpose({ refresh });
</script>

<template>
  <div class="flex flex-col gap-5">
    <section class="studio-card bg-white px-4 py-3.5 dark:bg-[#171a21]">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="min-w-0">
          <div class="flex items-center gap-2 text-sm font-semibold" :class="syncStateClass()">
            <RefreshCw class="size-4 shrink-0" :class="syncStatus?.status === 'running' ? 'animate-spin' : ''" />
            {{ syncStateLabel() }}
          </div>
          <p class="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
            <template v-if="syncStatus?.last_success_at">
              最近成功：{{ formatDateTime(syncStatus.last_success_at) }}，本次写入 {{ formatNumber(syncStatus.records_upserted) }} 条。
            </template>
            <template v-else>
              账单只保存在本地数据库中，浏览器不会接触中转站密钥。
            </template>
          </p>
          <p v-if="syncStatus?.last_error" class="mt-1 truncate text-xs text-rose-600 dark:text-rose-300" :title="syncStatus.last_error">
            {{ syncStatus.last_error }}
          </p>
        </div>
        <button
          type="button"
          class="studio-button inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45 dark:bg-white dark:text-slate-950"
          :disabled="syncing || syncStatus?.status === 'running' || !syncStatus?.enabled || !syncStatus?.configured"
          @click="runSync"
        >
          <RefreshCw class="size-4" :class="syncing ? 'animate-spin' : ''" />
          同步近 {{ syncDays }} 天
        </button>
      </div>
    </section>

    <div v-if="loadError" class="flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
      <span>加载上游费用失败：{{ loadError }}</span>
      <button type="button" class="studio-button inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white" @click="refresh(false)">
        <RefreshCw class="size-3.5" />
        重试
      </button>
    </div>

    <div v-if="loading && !summary" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div v-for="index in 4" :key="index" class="studio-skeleton h-28 rounded-2xl" />
    </div>

    <template v-else>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
          <div class="text-xs font-medium text-slate-500">实际费用</div>
          <div class="mt-2 whitespace-nowrap text-[28px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
            <AnimatedNumber :value="summary?.total_cost || 0" :formatter="formatCostNumber" />
          </div>
          <div class="mt-1 text-[11px] text-slate-400">
            <span v-if="summary?.unit && summary.unit !== 'mixed'">单位：{{ displayUnit(summary.unit) }} · </span>退款任务不会重复扣减
          </div>
        </div>
        <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
          <div class="text-xs font-medium text-slate-500">调用次数</div>
          <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
            <AnimatedNumber :value="summary?.record_count || 0" :formatter="formatNumber" />
          </div>
          <div class="mt-1 text-[11px] text-slate-400">
            成功 {{ formatNumber(summary?.success_count || 0) }} · 失败 {{ formatNumber(summary?.failed_count || 0) }}
          </div>
        </div>
        <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
          <div class="text-xs font-medium text-slate-500">退款</div>
          <div class="mt-2 whitespace-nowrap text-[28px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
            <AnimatedNumber :value="summary?.refunded_amount || 0" :formatter="formatCostNumber" />
          </div>
          <div class="mt-1 text-[11px] text-slate-400">
            {{ formatNumber(summary?.refunded_count || 0) }} 笔退款<span v-if="summary?.unit && summary.unit !== 'mixed'"> · 单位：{{ displayUnit(summary.unit) }}</span>
          </div>
        </div>
        <div class="studio-card bg-white px-4 py-4 dark:bg-[#171a21]">
          <div class="text-xs font-medium text-slate-500">未归属记录</div>
          <div class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
            <AnimatedNumber :value="summary?.unassigned_count || 0" :formatter="formatNumber" />
          </div>
          <div class="mt-1 text-[11px] text-slate-400">对话和音频需请求标识才能精确关联员工</div>
        </div>
      </div>

      <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <section class="studio-card overflow-hidden bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-end justify-between gap-4">
            <div>
              <h2 class="text-[20px] font-semibold">模型费用</h2>
              <p class="mt-1 text-[13px] text-slate-500">
                按上游模型汇总调用、实际扣费与退款<span v-if="summary?.unit && summary.unit !== 'mixed'">，单位：{{ displayUnit(summary.unit) }}</span>。
              </p>
            </div>
            <span class="text-xs text-slate-500">{{ formatNumber(modelCosts.length) }} 个模型</span>
          </div>
          <div v-if="modelCosts.length" class="mt-4 overflow-hidden rounded-xl border border-black/[0.06] dark:border-white/10">
            <table class="w-full table-fixed border-collapse text-[13px]">
              <colgroup>
                <col class="w-[30%]" />
                <col class="w-[13%]" />
                <col class="w-[13%]" />
                <col class="w-[18%]" />
                <col class="w-[26%]" />
              </colgroup>
              <thead>
                <tr class="border-b border-black/[0.06] bg-slate-50/70 text-left text-[11px] text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-stone-400">
                  <th class="px-4 py-2.5 font-semibold">模型</th>
                  <th class="px-2 py-2.5 font-semibold">类型</th>
                  <th class="px-2 py-2.5 text-right font-semibold">调用</th>
                  <th class="px-2 py-2.5 text-right font-semibold">成功 / 失败</th>
                  <th class="px-4 py-2.5 text-right font-semibold">费用 / 退款</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in modelCosts" :key="`${item.model_type}:${item.model}`" class="border-b border-black/[0.06] last:border-0 hover:bg-[#4F7CFF]/[0.035] dark:border-white/10 dark:hover:bg-white/[0.04]">
                  <td class="truncate px-4 py-3.5 font-mono font-semibold text-slate-900 dark:text-stone-100" :title="displayModel(item.model, '', item.model_version)">{{ displayModel(item.model, '', item.model_version) }}</td>
                  <td class="px-2 py-3.5"><span class="rounded-md px-1.5 py-1 text-[10px] font-semibold" :class="typeClass(item.model_type)">{{ typeLabel(item.model_type) }}</span></td>
                  <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">{{ formatNumber(item.count) }}</td>
                  <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">{{ formatNumber(item.success_count) }} / {{ formatNumber(item.failed_count) }}</td>
                  <td class="px-4 py-3.5 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                    {{ formatCostNumber(item.cost) }}
                    <div class="mt-0.5 text-[10px] font-normal text-slate-400">退 {{ formatCostNumber(item.refunded_amount) }} · {{ modelCostShare(item) }}%</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="mt-4 rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500 dark:border-white/10">当前范围暂无模型费用</div>
        </section>

        <section class="studio-card overflow-hidden bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-end justify-between gap-4">
            <div>
              <h2 class="text-[20px] font-semibold">费用归属</h2>
              <p class="mt-1 text-[13px] text-slate-500">
                通过上游 task_id 关联本地员工任务<span v-if="summary?.unit && summary.unit !== 'mixed'">，单位：{{ displayUnit(summary.unit) }}</span>。
              </p>
            </div>
            <span class="text-xs text-slate-500">{{ formatNumber(userCosts.length) }} 项</span>
          </div>
          <div v-if="userCosts.length" class="mt-4 overflow-hidden rounded-xl border border-black/[0.06] dark:border-white/10">
            <table class="w-full table-fixed border-collapse text-[13px]">
              <colgroup><col class="w-[42%]" /><col class="w-[16%]" /><col class="w-[22%]" /><col class="w-[20%]" /></colgroup>
              <thead>
                <tr class="border-b border-black/[0.06] bg-slate-50/70 text-left text-[11px] text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-stone-400">
                  <th class="px-4 py-2.5 font-semibold">用户</th>
                  <th class="px-2 py-2.5 text-right font-semibold">调用</th>
                  <th class="px-2 py-2.5 text-right font-semibold">费用</th>
                  <th class="px-4 py-2.5 text-right font-semibold">退款</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in userCosts" :key="item.owner_id || 'unassigned'" class="border-b border-black/[0.06] last:border-0 hover:bg-[#4F7CFF]/[0.035] dark:border-white/10 dark:hover:bg-white/[0.04]">
                  <td class="min-w-0 px-4 py-3.5">
                    <div class="truncate font-semibold text-slate-900 dark:text-stone-100">{{ item.name || item.username || "未归属" }}</div>
                    <div class="mt-0.5 truncate font-mono text-[11px] text-slate-400">{{ item.owner_id || "未匹配本地任务" }}</div>
                  </td>
                  <td class="px-2 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">{{ formatNumber(item.count) }}</td>
                  <td class="px-2 py-3.5 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">{{ formatCostNumber(item.cost) }}</td>
                  <td class="px-4 py-3.5 text-right tabular-nums text-slate-600 dark:text-stone-300">{{ formatCostNumber(item.refunded_amount) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="mt-4 rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500 dark:border-white/10">当前范围暂无归属数据</div>
        </section>
      </div>

      <section class="studio-card bg-white p-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h2 class="text-[20px] font-semibold">上游费用明细</h2>
            <p class="mt-1 text-[13px] text-slate-500">{{ rangeLabel }} 的账单明细，退款额单列且不会再次从实际费用中扣除。</p>
          </div>
          <div class="flex w-full flex-col gap-2 sm:flex-row xl:w-auto">
            <div class="relative w-full sm:w-[320px]">
              <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <input v-model="query" class="studio-input h-11 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索用户、模型、渠道或 task_id" aria-label="搜索费用明细" />
            </div>
            <button type="button" class="studio-button inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-black/[0.06] px-3 text-sm font-medium disabled:opacity-45 dark:border-white/10" :disabled="!records.length" @click="exportCsv">
              <Download class="size-4" />
              导出 CSV
            </button>
          </div>
        </div>

        <div v-if="records.length" class="mt-4 overflow-x-auto">
          <table class="w-full min-w-[1420px] border-collapse text-sm">
            <thead>
              <tr class="border-b border-black/[0.06] text-left text-[11px] font-semibold text-slate-400 dark:border-white/10">
                <th class="px-3 py-2 font-semibold">完成时间</th>
                <th class="px-3 py-2 font-semibold">类型</th>
                <th class="px-3 py-2 font-semibold">状态</th>
                <th class="px-3 py-2 font-semibold">用户</th>
                <th class="px-3 py-2 font-semibold">模型</th>
                <th class="px-3 py-2 text-right font-semibold">实际费用</th>
                <th class="px-3 py-2 text-right font-semibold">退款</th>
                <th class="px-3 py-2 font-semibold">渠道</th>
                <th class="px-3 py-2 font-semibold">上游 task_id</th>
                <th class="px-3 py-2 font-semibold">本地 task_id</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in records" :key="item.row_key" class="studio-row-enter border-b border-black/[0.06] last:border-0 dark:border-white/10" :style="{ '--studio-row-index': index }">
                <td class="whitespace-nowrap px-3 py-3 text-slate-600 dark:text-stone-300">{{ formatDateTime(item.completed_at || item.event_at) }}</td>
                <td class="px-3 py-3"><span class="rounded-md px-2 py-1 text-[11px] font-semibold" :class="typeClass(item.model_type)">{{ typeLabel(item.model_type) }}</span></td>
                <td class="px-3 py-3"><span class="rounded-md px-2 py-1 text-[11px] font-semibold" :class="stateClass(item.state)">{{ stateLabel(item.state) }}</span></td>
                <td class="px-3 py-3">
                  <div class="font-medium text-slate-900 dark:text-stone-100">{{ item.owner_name || "未归属" }}</div>
                  <div class="font-mono text-[11px] text-slate-400">{{ item.owner_id || "未匹配" }}</div>
                </td>
                <td class="max-w-[220px] truncate px-3 py-3 font-mono text-[13px] text-slate-700 dark:text-stone-200" :title="displayModel(item.model, item.requested_model, item.model_version)">{{ displayModel(item.model, item.requested_model, item.model_version) }}</td>
                <td class="px-3 py-3 text-right font-semibold tabular-nums text-slate-950 dark:text-stone-50">{{ formatCost(item.cost, item.unit) }}</td>
                <td class="px-3 py-3 text-right tabular-nums" :class="item.refunded ? 'font-semibold text-emerald-700 dark:text-emerald-300' : 'text-slate-400'">{{ item.refunded ? formatCost(item.refunded_amount, item.unit) : "--" }}</td>
                <td class="max-w-[180px] truncate px-3 py-3 text-[12px] text-slate-500" :title="item.channel_group">{{ item.channel_group || "--" }}</td>
                <td class="max-w-[220px] truncate px-3 py-3 font-mono text-[11px] text-slate-500" :title="item.upstream_task_id">{{ item.upstream_task_id }}</td>
                <td class="max-w-[220px] truncate px-3 py-3 font-mono text-[11px] text-slate-500" :title="item.local_task_id">{{ item.local_task_id || "未关联" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="records.length" class="mt-4 flex flex-col gap-3 border-t border-black/[0.06] pt-4 text-sm text-slate-500 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
          <span>显示 {{ formatNumber(pageStart) }}-{{ formatNumber(pageEnd) }} / {{ formatNumber(details?.record_count || 0) }} 条记录，每页 20 条</span>
          <div class="flex items-center gap-2">
            <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10" :disabled="page <= 1 || busy" aria-label="上一页" @click="goToPage(page - 1)"><ChevronLeft class="size-4" /></button>
            <span class="min-w-20 text-center font-semibold tabular-nums text-slate-900 dark:text-stone-100">{{ page }} / {{ totalPages }}</span>
            <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10" :disabled="page >= totalPages || busy" aria-label="下一页" @click="goToPage(page + 1)"><ChevronRight class="size-4" /></button>
          </div>
        </div>
        <div v-else class="mt-4 flex flex-col items-center rounded-xl border border-dashed border-slate-300 px-4 py-10 text-center dark:border-white/10">
          <AlertCircle v-if="loadError" class="mb-2 size-5 text-rose-500" />
          <CheckCircle2 v-else class="mb-2 size-5 text-slate-400" />
          <span class="text-sm text-slate-500">{{ query ? "没有匹配的费用记录" : "当前筛选条件下暂无上游费用记录" }}</span>
        </div>
      </section>
    </template>
  </div>
</template>
