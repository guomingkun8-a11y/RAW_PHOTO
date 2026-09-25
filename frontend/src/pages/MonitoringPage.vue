<script setup lang="ts">
import {
  Activity,
  AudioLines,
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
  Bot,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CloudUpload,
  Download,
  Gauge,
  Images,
  RefreshCw,
  Save,
  Search,
  Server,
  Video,
  Wifi,
  WifiOff,
  Workflow,
  X,
  Zap,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import AnimatedNumber from "@/components/AnimatedNumber.vue";
import AnimatedSuccessRing from "@/components/AnimatedSuccessRing.vue";
import ReferenceImagePreview from "@/components/image/ReferenceImagePreview.vue";
import { useVisibilityAwareInterval } from "@/composables/useVisibilityAwareInterval";
import {
  fetchMonitoringSummary,
  fetchMonitoringTasks,
  resolveApiAssetUrl,
  type MonitoringLatencySummary,
  type MonitoringAgentQueueSummary,
  type MonitoringQueueSummary,
  type MonitoringSummary,
  type MonitoringTaskDetail,
  type MonitoringTaskReferenceImage,
  type MonitoringTaskDetails,
  type MonitoringSource,
  type MonitoringUserStat,
} from "@/lib/api";
import { storageKey } from "@/lib/storage-namespace";

const summary = ref<MonitoringSummary | null>(null);
const query = ref("");
const loading = ref(true);
const refreshing = ref(false);
const lastUpdated = ref("");
const loadError = ref("");
const autoRefresh = ref(true);
let requestSequence = 0;
const route = useRoute();
const router = useRouter();
const MONITORING_AUTO_REFRESH_STORAGE_KEY = storageKey("raw-monitoring-auto-refresh");
const { restart: restartAutoRefresh } = useVisibilityAwareInterval(() => load(true), 15_000);

type SortDirection = "asc" | "desc";
type UserSortKey = "success" | "failed" | "running" | "queued" | "total" | "cost" | "load";
type RangePreset = "today" | "7d" | "30d" | "all" | "custom";
type UserScope = "with-output" | "online" | "all";
type DetailStatus = "all" | "success" | "error";

const rangePreset = ref<RangePreset>("today");
const monitoringSource = ref<MonitoringSource>("image");

const sourceOptions = [
  { value: "image" as const, label: "生图监控", icon: Images },
  { value: "video" as const, label: "生视频监控", icon: Video },
  { value: "audio" as const, label: "音频监控", icon: AudioLines },
];

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

const customStart = ref(toLocalDateTimeValue(startOfToday()));
const customEnd = ref(toLocalDateTimeValue(addDays(startOfToday(), 1)));
const customStartDate = ref(toLocalDateValue(startOfToday()));
const customEndDate = ref(toLocalDateValue(startOfToday()));
const preciseTime = ref(false);
const appliedRange = ref<{ startAt?: string; endAt?: string }>({});
const appliedRangeLabel = ref("今天");

const rangePresets: { value: RangePreset; label: string }[] = [
  { value: "today", label: "今天" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "all", label: "全部" },
  { value: "custom", label: "自定义" },
];

const userScopes: { value: UserScope; label: string }[] = [
  { value: "with-output", label: "有生成记录" },
  { value: "online", label: "在线用户" },
  { value: "all", label: "全部用户" },
];

const userSortKey = ref<UserSortKey>("load");
const userSortDirection = ref<SortDirection>("desc");
const userScope = ref<UserScope>("with-output");
const userPage = ref(1);
const selectedDetails = ref<MonitoringTaskDetails | null>(null);
const detailLoading = ref(false);
const detailLoadingMore = ref(false);
const detailError = ref("");
const detailPanelOpen = ref(false);
const selectedDetailStatus = ref<DetailStatus>("all");
const selectedDetailUser = ref<MonitoringUserStat | null>(null);
const imageGalleryOpen = ref(false);
const imageGalleryLoading = ref(false);
const imageGalleryLoadingMore = ref(false);
const imageGalleryError = ref("");
const imageGalleryDetails = ref<MonitoringTaskDetails | null>(null);
const imageGalleryUser = ref<MonitoringUserStat | null>(null);
const referenceGalleryOpen = ref(false);
const referenceGalleryLoading = ref(false);
const referenceGalleryLoadingMore = ref(false);
const referenceGalleryError = ref("");
const referenceGalleryDetails = ref<MonitoringTaskDetails | null>(null);
const referenceGalleryUser = ref<MonitoringUserStat | null>(null);
const expandedMobileUserId = ref("");
const referencePreview = ref<{ previewUrl: string; label: string; subtitle: string } | null>(null);
const USER_PAGE_SIZE = 20;
const MONITORING_DETAIL_PAGE_SIZE = 60;

const userSortColumns: { key: UserSortKey; label: string }[] = [
  { key: "success", label: "成功" },
  { key: "failed", label: "失败" },
  { key: "running", label: "进行中" },
  { key: "queued", label: "排队中" },
  { key: "total", label: "总生成" },
  { key: "cost", label: "费用" },
];

const numberFormat = new Intl.NumberFormat("zh-CN");
const costFormat = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 6,
});

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

function userCostTotal(item: MonitoringUserStat) {
  return numericCost(item.cost_total);
}

function userHasTaskHistory(item: MonitoringUserStat) {
  return userVolume(item) > 0 || userLoad(item) > 0;
}

function userSortValue(item: MonitoringUserStat, key: UserSortKey) {
  if (key === "success") return item.success_count;
  if (key === "failed") return item.failed_count;
  if (key === "running") return item.running_tasks;
  if (key === "queued") return item.queued_tasks;
  if (key === "total") return userVolume(item);
  if (key === "cost") return userCostTotal(item);
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

function isValidRangePreset(value: unknown): value is RangePreset {
  return ["today", "7d", "30d", "all", "custom"].includes(String(value));
}

function isValidMonitoringSource(value: unknown): value is MonitoringSource {
  return value === "image" || value === "video" || value === "audio";
}

function restoreRangeFromUrl() {
  monitoringSource.value = isValidMonitoringSource(route.query.source) ? route.query.source : "image";
  const preset = String(route.query.range || "today");
  rangePreset.value = isValidRangePreset(preset) ? preset : "today";
  if (rangePreset.value !== "custom") return;
  const start = String(route.query.start || "");
  const end = String(route.query.end || "");
  preciseTime.value = String(route.query.precise || "") === "1";
  if (preciseTime.value) {
    if (start) customStart.value = start;
    if (end) customEnd.value = end;
  } else {
    if (start) customStartDate.value = start.slice(0, 10);
    if (end) customEndDate.value = end.slice(0, 10);
  }
}

function syncRangeUrl() {
  const nextQuery = { ...route.query, range: rangePreset.value, source: monitoringSource.value } as Record<string, string | undefined>;
  if (rangePreset.value === "custom") {
    nextQuery.start = preciseTime.value ? customStart.value : customStartDate.value;
    nextQuery.end = preciseTime.value ? customEnd.value : customEndDate.value;
    if (preciseTime.value) nextQuery.precise = "1";
    else delete nextQuery.precise;
  } else {
    delete nextQuery.start;
    delete nextQuery.end;
    delete nextQuery.precise;
  }
  void router.replace({ query: nextQuery });
}

function sourceLabel() {
  return {
    image: "生图",
    video: "生视频",
    audio: "音频生成",
  }[monitoringSource.value];
}

function sourceUnit() {
  return {
    image: "图",
    video: "视频",
    audio: "音频",
  }[monitoringSource.value];
}

function sourceCountUnit() {
  return {
    image: "张图",
    video: "个视频",
    audio: "条音频",
  }[monitoringSource.value];
}

function selectMonitoringSource(value: MonitoringSource) {
  if (monitoringSource.value === value) return;
  monitoringSource.value = value;
  selectedDetails.value = null;
  detailPanelOpen.value = false;
  imageGalleryOpen.value = false;
  referenceGalleryOpen.value = false;
  imageGalleryDetails.value = null;
  referenceGalleryDetails.value = null;
  referencePreview.value = null;
  userPage.value = 1;
  syncRangeUrl();
  void load(true);
}

function rangeForSelection() {
  if (rangePreset.value === "all") return {};
  if (rangePreset.value === "custom") {
    if (preciseTime.value) return { startAt: customStart.value, endAt: customEnd.value };
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

function selectedRange() {
  return appliedRange.value;
}

function commitRangeSelection() {
  appliedRange.value = rangeForSelection();
  if (rangePreset.value === "all") appliedRangeLabel.value = "全部历史";
  else if (rangePreset.value === "today") appliedRangeLabel.value = "今天";
  else if (rangePreset.value === "7d") appliedRangeLabel.value = "最近 7 个自然日";
  else if (rangePreset.value === "30d") appliedRangeLabel.value = "最近 30 个自然日";
  else if (preciseTime.value) {
    appliedRangeLabel.value = `${formatRangeValue(customStart.value)} 至 ${formatRangeValue(customEnd.value)}`;
  } else {
    appliedRangeLabel.value = customStartDate.value === customEndDate.value
      ? customStartDate.value
      : `${customStartDate.value} 至 ${customEndDate.value}`;
  }
}

function formatRangeValue(value: string) {
  if (!value) return "";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

const activeRangeLabel = computed(() => {
  return appliedRangeLabel.value;
});

async function load(silent = false) {
  const sequence = ++requestSequence;
  silent ? (refreshing.value = true) : (loading.value = true);
  try {
    const nextSummary = await fetchMonitoringSummary({ ...selectedRange(), source: monitoringSource.value });
    if (sequence !== requestSequence) return;
    summary.value = nextSummary;
    loadError.value = "";
    lastUpdated.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  } catch (error) {
    if (sequence !== requestSequence) return;
    loadError.value = error instanceof Error ? error.message : "读取监控数据失败";
    toast.error(error instanceof Error ? error.message : "读取监控数据失败");
  } finally {
    if (sequence !== requestSequence) return;
    loading.value = false;
    refreshing.value = false;
  }
}

function selectRangePreset(value: RangePreset) {
  rangePreset.value = value;
  if (value === "custom") return;
  commitRangeSelection();
  syncRangeUrl();
  void load(true);
}

function applyCustomRange() {
  const startValue = preciseTime.value ? customStart.value : customStartDate.value;
  const endValue = preciseTime.value ? customEnd.value : customEndDate.value;
  if (!startValue || !endValue) {
    toast.error("请选择开始和结束时间");
    return;
  }
  const startTime = new Date(preciseTime.value ? customStart.value : `${customStartDate.value}T00:00`).getTime();
  const endTime = new Date(preciseTime.value ? customEnd.value : `${customEndDate.value}T23:59:59`).getTime();
  if (startTime >= endTime) {
    toast.error(preciseTime.value ? "结束时间必须晚于开始时间" : "结束日期不能早于开始日期");
    return;
  }
  commitRangeSelection();
  syncRangeUrl();
  void load(true);
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value;
  try {
    window.localStorage.setItem(MONITORING_AUTO_REFRESH_STORAGE_KEY, String(autoRefresh.value));
  } catch {
    // Local storage can be unavailable in private or embedded browser contexts.
  }
  restartAutoRefresh(autoRefresh.value);
}

function taskStatusLabel(status: DetailStatus) {
  return status === "success" ? "成功任务" : status === "error" ? "失败任务" : "全部任务";
}

function detailTitle() {
  const userName = selectedDetailUser.value?.name || selectedDetailUser.value?.username || "全部用户";
  return `${userName} · ${taskStatusLabel(selectedDetailStatus.value)}`;
}

async function openTaskDetails(user: MonitoringUserStat | null, status: DetailStatus) {
  detailPanelOpen.value = true;
  imageGalleryOpen.value = false;
  referenceGalleryOpen.value = false;
  selectedDetailUser.value = user;
  selectedDetailStatus.value = status;
  selectedDetails.value = null;
  detailError.value = "";
  detailLoadingMore.value = false;
  detailLoading.value = true;
  try {
    selectedDetails.value = await fetchMonitoringTasks({
      ...selectedRange(),
      source: monitoringSource.value,
      ownerId: user?.user_id,
      status,
      limit: MONITORING_DETAIL_PAGE_SIZE,
      includeReferences: true,
    });
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : "读取任务明细失败";
    toast.error(detailError.value);
  } finally {
    detailLoading.value = false;
  }
}

function monitoringDetailsHasMore(details: MonitoringTaskDetails | null) {
  return Boolean(details && (details.has_more ?? details.truncated));
}

function mergeMonitoringDetails(current: MonitoringTaskDetails, next: MonitoringTaskDetails): MonitoringTaskDetails {
  const seen = new Set(current.items.map((item) => item.row_key));
  return {
    ...next,
    items: [...current.items, ...next.items.filter((item) => !seen.has(item.row_key))],
    offset: 0,
  };
}

async function loadMoreTaskDetails() {
  const current = selectedDetails.value;
  if (!current || !monitoringDetailsHasMore(current) || detailLoadingMore.value) return;
  detailLoadingMore.value = true;
  try {
    const next = await fetchMonitoringTasks({
      ...selectedRange(),
      source: monitoringSource.value,
      ownerId: selectedDetailUser.value?.user_id,
      status: selectedDetailStatus.value,
      limit: MONITORING_DETAIL_PAGE_SIZE,
      cursor: current.next_cursor,
      includeReferences: true,
    });
    selectedDetails.value = mergeMonitoringDetails(current, next);
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : "读取更多任务明细失败";
    toast.error(detailError.value);
  } finally {
    detailLoadingMore.value = false;
  }
}

function closeTaskDetails() {
  detailPanelOpen.value = false;
  selectedDetails.value = null;
  detailError.value = "";
  detailLoadingMore.value = false;
}

function taskDetailReferenceItems(item: MonitoringTaskDetail) {
  return (item.reference_images || [])
    .map((asset, index) => ({
      key: `${item.row_key}:reference:${index}:${asset.preview_url}`,
      previewUrl: resolveApiAssetUrl(asset.preview_url),
      label: asset.filename || asset.role || `reference-${index + 1}`,
      role: asset.role || "",
    }))
    .filter((asset) => asset.previewUrl);
}
function openReferencePreview(reference: { previewUrl: string; label: string; role?: string; taskId?: string; completedAt?: string }) {
  referencePreview.value = {
    previewUrl: reference.previewUrl,
    label: reference.label || "上传参考图",
    subtitle: [reference.taskId, reference.completedAt, reference.role].filter(Boolean).join(" · "),
  };
}

function openGeneratedPreview(item: Pick<MonitoringTaskDetail, "image_url" | "task_id" | "completed_at" | "model">) {
  const previewUrl = resolveApiAssetUrl(item.image_url);
  if (!previewUrl) return;
  referencePreview.value = {
    previewUrl,
    label: "生成图片",
    subtitle: [item.task_id, item.completed_at, item.model].filter(Boolean).join(" / "),
  };
}

function imageGalleryTitle() {
  const userName = imageGalleryUser.value?.name || imageGalleryUser.value?.username || "用户";
  return `${userName} · 生成图片`;
}

const imageGalleryItems = computed(() =>
  (imageGalleryDetails.value?.items || []).filter((item) => item.status === "success" && item.image_url),
);

async function openUserImageGallery(user: MonitoringUserStat) {
  imageGalleryOpen.value = true;
  detailPanelOpen.value = false;
  referenceGalleryOpen.value = false;
  imageGalleryUser.value = user;
  imageGalleryDetails.value = null;
  imageGalleryError.value = "";
  imageGalleryLoadingMore.value = false;
  imageGalleryLoading.value = true;
  try {
    imageGalleryDetails.value = await fetchMonitoringTasks({
      ...selectedRange(),
      source: "image",
      ownerId: user.user_id,
      status: "success",
      limit: MONITORING_DETAIL_PAGE_SIZE,
    });
  } catch (error) {
    imageGalleryError.value = error instanceof Error ? error.message : "读取生成图片失败";
    toast.error(imageGalleryError.value);
  } finally {
    imageGalleryLoading.value = false;
  }
}

async function loadMoreUserImages() {
  const current = imageGalleryDetails.value;
  const user = imageGalleryUser.value;
  if (!current || !user || !monitoringDetailsHasMore(current) || imageGalleryLoadingMore.value) return;
  imageGalleryLoadingMore.value = true;
  try {
    const next = await fetchMonitoringTasks({
      ...selectedRange(),
      source: "image",
      ownerId: user.user_id,
      status: "success",
      limit: MONITORING_DETAIL_PAGE_SIZE,
      cursor: current.next_cursor,
    });
    imageGalleryDetails.value = mergeMonitoringDetails(current, next);
  } catch (error) {
    imageGalleryError.value = error instanceof Error ? error.message : "读取更多生成图片失败";
    toast.error(imageGalleryError.value);
  } finally {
    imageGalleryLoadingMore.value = false;
  }
}

function closeUserImageGallery() {
  imageGalleryOpen.value = false;
  imageGalleryDetails.value = null;
  imageGalleryError.value = "";
  imageGalleryLoadingMore.value = false;
  referenceGalleryOpen.value = false;
  referenceGalleryDetails.value = null;
  referenceGalleryError.value = "";
  referenceGalleryLoadingMore.value = false;
}

function referenceGalleryTitle() {
  const userName = referenceGalleryUser.value?.name || referenceGalleryUser.value?.username || "用户";
  return `${userName} · 上传参考图`;
}

const referenceGalleryItems = computed(() => {
  const seen = new Set<string>();
  const items: Array<{
    key: string;
    previewUrl: string;
    label: string;
    taskId: string;
    completedAt: string;
    mode: string;
    model: string;
    role: string;
    kind: string;
  }> = [];
  for (const task of referenceGalleryDetails.value?.items || []) {
    for (const asset of (task.reference_images || []) as MonitoringTaskReferenceImage[]) {
      const previewUrl = resolveApiAssetUrl(asset.preview_url);
      if (!previewUrl) continue;
      const key = `${task.task_id}:${previewUrl}`;
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({
        key,
        previewUrl,
        label: asset.filename || asset.role || task.task_id,
        taskId: task.task_id,
        completedAt: task.completed_at,
        mode: task.mode,
        model: task.model,
        role: asset.role || "",
        kind: asset.kind || "",
      });
    }
  }
  return items;
});

async function openUserReferenceGallery(user: MonitoringUserStat) {
  referenceGalleryOpen.value = true;
  detailPanelOpen.value = false;
  imageGalleryOpen.value = false;
  referenceGalleryUser.value = user;
  referenceGalleryDetails.value = null;
  referenceGalleryError.value = "";
  referenceGalleryLoadingMore.value = false;
  referenceGalleryLoading.value = true;
  try {
    referenceGalleryDetails.value = await fetchMonitoringTasks({
      ...selectedRange(),
      source: "image",
      ownerId: user.user_id,
      status: "all",
      limit: MONITORING_DETAIL_PAGE_SIZE,
      includeReferences: true,
    });
  } catch (error) {
    referenceGalleryError.value = error instanceof Error ? error.message : "读取上传参考图失败";
    toast.error(referenceGalleryError.value);
  } finally {
    referenceGalleryLoading.value = false;
  }
}

async function loadMoreUserReferences() {
  const current = referenceGalleryDetails.value;
  const user = referenceGalleryUser.value;
  if (!current || !user || !monitoringDetailsHasMore(current) || referenceGalleryLoadingMore.value) return;
  referenceGalleryLoadingMore.value = true;
  try {
    const next = await fetchMonitoringTasks({
      ...selectedRange(),
      source: "image",
      ownerId: user.user_id,
      status: "all",
      limit: MONITORING_DETAIL_PAGE_SIZE,
      cursor: current.next_cursor,
      includeReferences: true,
    });
    referenceGalleryDetails.value = mergeMonitoringDetails(current, next);
  } catch (error) {
    referenceGalleryError.value = error instanceof Error ? error.message : "读取更多上传参考图失败";
    toast.error(referenceGalleryError.value);
  } finally {
    referenceGalleryLoadingMore.value = false;
  }
}

function closeUserReferenceGallery() {
  referenceGalleryOpen.value = false;
  referenceGalleryDetails.value = null;
  referenceGalleryError.value = "";
  referenceGalleryLoadingMore.value = false;
}

function detailOwnerName(item: MonitoringTaskDetail) {
  const owner = summary.value?.users.find((user) => user.user_id === item.owner_id);
  return owner?.name || owner?.username || item.owner_id;
}

function toggleMobileUser(user: MonitoringUserStat) {
  expandedMobileUserId.value = expandedMobileUserId.value === user.user_id ? "" : user.user_id;
}

function csvCell(value: unknown) {
  const textValue = String(value ?? "").replace(/"/g, '""');
  return `"${textValue}"`;
}

function exportUsersCsv() {
  const rows = [
    ["用户", "用户名", "角色", "在线状态", `成功${sourceUnit()}`, `失败${sourceUnit()}`, `合计${sourceUnit()}`, "费用", "计费次数", "平均费用", "运行中", "排队中", "当前负载"],
    ...sortedUsers.value.map((user) => [
      user.name || user.username,
      user.username,
      roleLabel(user.role),
      user.online ? "在线" : "离线",
      user.success_count,
      user.failed_count,
      userVolume(user),
      formatCost(user.cost_total),
      user.cost_count || 0,
      formatCost(user.cost_average),
      user.running_tasks,
      user.queued_tasks,
      userLoad(user),
    ]),
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `raw-monitoring-${rangePreset.value}-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast.success(`已导出 ${sortedUsers.value.length} 个用户的统计`);
}

const queue = computed<MonitoringQueueSummary | null>(() => summary.value?.task_queue || null);
const agentQueue = computed<MonitoringAgentQueueSummary | null>(() => summary.value?.agent_queue || null);
const latency = computed<MonitoringLatencySummary | null>(() => summary.value?.task_latency || null);
const stageLatency = computed(() => summary.value?.stage_latency || null);
const isImageSource = computed(() => monitoringSource.value === "image");
const isVideoSource = computed(() => monitoringSource.value === "video");
const isAudioSource = computed(() => monitoringSource.value === "audio");

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
  const users = (summary.value?.users || []).filter((item) => {
    if (userScope.value === "online") return item.online;
    if (userScope.value === "with-output") return userVolume(item) > 0 || userLoad(item) > 0;
    return true;
  });
  return keyword ? users.filter((item) => matchesQuery(item, keyword)) : users;
});

const usersByLoad = computed(() => sortUsers(filteredUsers.value, "load", "desc"));
const sortedUsers = computed(() => sortUsers(filteredUsers.value, userSortKey.value, userSortDirection.value, true));
const userTotalPages = computed(() => Math.max(1, Math.ceil(sortedUsers.value.length / USER_PAGE_SIZE)));
const userPageStart = computed(() => sortedUsers.value.length ? (userPage.value - 1) * USER_PAGE_SIZE + 1 : 0);
const userPageEnd = computed(() => Math.min(userPage.value * USER_PAGE_SIZE, sortedUsers.value.length));
const paginatedUsers = computed(() =>
  sortedUsers.value.slice(
    (userPage.value - 1) * USER_PAGE_SIZE,
    userPage.value * USER_PAGE_SIZE,
  ),
);

function goToUserPage(page: number) {
  userPage.value = Math.min(userTotalPages.value, Math.max(1, page));
}

const totalSuccess = computed(() => summary.value?.total_success || 0);
const totalFailed = computed(() => summary.value?.total_failed || 0);
const totalGenerated = computed(() => totalSuccess.value + totalFailed.value);
const totalCost = computed(() => numericCost(summary.value?.total_cost));
const totalCostCount = computed(() => summary.value?.cost_count || 0);
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
          label: "当前排队",
          value: queue.value.queue_depth,
          tone: "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
        },
        {
          label: "运行中",
          value: queue.value.running_tasks,
          tone: "bg-[#4F7CFF]/10 text-[#315be8]",
        },
        {
          label: "并发槽位",
          value: queue.value.active_slots,
          denominator: queue.value.slot_limit,
          tone: "bg-[#6D5EF7]/10 text-[#6D5EF7]",
        },
        {
          label: "单用户上限",
          value: queue.value.effective_owner_concurrency || queue.value.owner_concurrency,
          tone: "bg-sky-50 text-sky-700",
        },
      ]
    : [],
);

const agentQueueState = computed(() => {
  const data = agentQueue.value;
  if (!data?.enabled) return { label: "未启用", tone: "text-slate-500 bg-slate-100 dark:bg-white/[0.06]" };
  if (data.available === false) return { label: "不可用", tone: "text-rose-700 bg-rose-50 dark:bg-rose-400/10 dark:text-rose-300" };
  if ((data.lag || 0) > 0 || (data.pending || 0) > 0) {
    return { label: "处理中", tone: "text-amber-700 bg-amber-50 dark:bg-amber-400/10 dark:text-amber-300" };
  }
  return { label: "正常", tone: "text-emerald-700 bg-emerald-50 dark:bg-emerald-400/10 dark:text-emerald-300" };
});

const agentQueueMetrics = computed(() => {
  const data = agentQueue.value;
  if (!data) return [];
  return [
    { label: "等待", value: data.lag || 0 },
    { label: "执行中", value: data.active || 0 },
    { label: "工作进程", value: data.activeWorkers || 0 },
    { label: "并发", value: data.active || 0, denominator: data.totalConcurrency || 0 },
  ];
});

const stageMetrics = computed(() => {
  const stages = stageLatency.value;
  if (!stages) return [];
  return [
    { label: isAudioSource.value ? "文本提交" : "参考图上传", value: stages.upload, icon: CloudUpload, tone: "text-sky-600" },
    { label: "队列等待", value: stages.queue, icon: Workflow, tone: "text-amber-600" },
    { label: isVideoSource.value ? "视频生成" : isAudioSource.value ? "音频生成" : "上游生成", value: stages.generation, icon: Zap, tone: "text-[#4F7CFF]" },
    { label: "结果保存", value: stages.save, icon: Save, tone: "text-emerald-600" },
  ].filter((item) => item.value.sample_size > 0);
});

onMounted(() => {
  restoreRangeFromUrl();
  commitRangeSelection();
  try {
    autoRefresh.value = window.localStorage.getItem(MONITORING_AUTO_REFRESH_STORAGE_KEY) !== "false";
  } catch {
    autoRefresh.value = true;
  }
  void load();
  restartAutoRefresh(autoRefresh.value);
});

watch([query, userScope, userSortKey, userSortDirection], () => {
  userPage.value = 1;
});

watch(userTotalPages, (totalPages) => {
  if (userPage.value > totalPages) userPage.value = totalPages;
});

</script>

<template>
  <section class="monitoring-page min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <div class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div class="space-y-2">
            <div class="inline-flex items-center gap-2 rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
              <Server class="size-3.5" />
              实时运行
            </div>
            <h1 class="text-[30px] font-semibold text-slate-950 dark:text-stone-50">运行监控</h1>
            <p class="max-w-3xl text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              {{ sourceLabel() }}运行状态每 15 秒刷新，生成量与耗时按所选日期统计。最近更新 {{ lastUpdated || "暂无" }}。
            </p>
            <div class="inline-flex max-w-full overflow-x-auto rounded-xl bg-[#F1F5F9] p-1 dark:bg-white/[0.06]" aria-label="监控来源">
              <button
                v-for="option in sourceOptions"
                :key="option.value"
                type="button"
                class="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-[13px] font-medium text-slate-500 transition-colors"
                :class="monitoringSource === option.value ? 'bg-white text-[#315be8] shadow-sm dark:bg-white/10 dark:text-white' : 'hover:text-slate-900 dark:hover:text-white'"
                :aria-pressed="monitoringSource === option.value"
                @click="selectMonitoringSource(option.value)"
              >
                <component :is="option.icon" class="size-4" />
                {{ option.label }}
              </button>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <span class="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600 dark:bg-white/[0.06] dark:text-slate-300">
              <span class="size-2 rounded-full bg-[#4F7CFF]" />
              {{ queueState.label }}
            </span>
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
              :disabled="loading || refreshing"
              @click="load(true)"
            >
              <RefreshCw class="size-4" :class="loading || refreshing ? 'animate-spin' : ''" />
              手动刷新
            </button>
          </div>
        </div>

        <div class="mt-5 flex flex-col gap-4 border-t border-black/[0.06] pt-4 dark:border-white/10 2xl:flex-row 2xl:items-end 2xl:justify-between">
          <div>
            <div class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-stone-100">
              <CalendarDays class="size-4 text-[#4F7CFF]" />
              历史统计范围
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
            <label class="inline-flex h-10 cursor-pointer items-center gap-2 text-xs font-medium text-slate-500">
              <input v-model="preciseTime" type="checkbox" class="size-4 accent-[#315be8]" />
              精确到时间
            </label>
            <label class="text-xs font-medium text-slate-500">
              开始{{ preciseTime ? '时间' : '日期' }}
              <input v-if="preciseTime" v-model="customStart" type="datetime-local" class="studio-input mt-1 block h-10 min-w-[205px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
              <input v-else v-model="customStartDate" type="date" class="studio-input mt-1 block h-10 min-w-[170px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
            </label>
            <label class="text-xs font-medium text-slate-500">
              结束{{ preciseTime ? '时间' : '日期' }}
              <input v-if="preciseTime" v-model="customEnd" type="datetime-local" class="studio-input mt-1 block h-10 min-w-[205px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
              <input v-else v-model="customEndDate" type="date" class="studio-input mt-1 block h-10 min-w-[170px] bg-[#F8FAFC] px-3 dark:bg-white/[0.04]" />
            </label>
            <button type="button" class="studio-button h-10 min-w-[72px] bg-slate-950 px-4 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" @click="applyCustomRange">
              应用
            </button>
          </div>

          <div v-else class="text-sm text-slate-500">
            当前统计：<span class="font-semibold text-slate-900 dark:text-stone-100">{{ activeRangeLabel }}</span>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2 px-1 text-[13px] text-slate-500 dark:text-stone-400">
        <span class="font-semibold text-slate-900 dark:text-stone-100">实时运行状态</span>
        <span>队列、在线用户、运行中任务和并发占用</span>
        <span class="text-slate-300 dark:text-stone-600">/</span>
        <span class="font-semibold text-slate-900 dark:text-stone-100">历史统计</span>
        <span>按 {{ activeRangeLabel }} 统计成功、失败和耗时</span>
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
                <span v-if="queue?.queue_depths && Object.keys(queue.queue_depths).length">标准 {{ formatNumber(queue.queue_depths.standard || 0) }} / 智能体 {{ formatNumber(queue.queue_depths.agent || 0) }} / 批量 {{ formatNumber(queue.queue_depths.batch || 0) }}。</span>
              </p>
            </div>
            <div class="text-right text-xs text-slate-500">
              <div>状态刷新 {{ lastUpdated || "暂无" }}</div>
              <div>{{ autoRefresh ? '每 15 秒自动更新' : '自动刷新已关闭' }}</div>
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
              <p class="mt-1 text-xl font-semibold leading-none tabular-nums">
                <AnimatedNumber :value="item.value" :formatter="formatNumber" />
                <template v-if="typeof item.denominator === 'number'">
                  /<AnimatedNumber :value="item.denominator" :formatter="formatNumber" />
                </template>
              </p>
            </div>
          </div>

          <div v-if="agentQueue && isImageSource" class="mt-4 border-t border-black/[0.06] pt-4 dark:border-white/10">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="flex min-w-0 items-start gap-2.5">
                <Bot class="mt-0.5 size-4 shrink-0 text-[#4F7CFF]" />
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h3 class="text-[14px] font-semibold text-slate-900 dark:text-stone-100">智能体队列</h3>
                    <span class="rounded-md px-2 py-0.5 text-[11px] font-semibold" :class="agentQueueState.tone">
                      {{ agentQueueState.label }}
                    </span>
                  </div>
                  <p class="mt-1 text-[12px] leading-5 text-slate-500 dark:text-stone-400">
                    <template v-if="agentQueue.available === false">{{ agentQueue.error || "Redis 队列不可用" }}</template>
                    <template v-else>单用户并发 {{ formatNumber(agentQueue.ownerConcurrency || 0) }}，单用户最多等待 {{ formatNumber(agentQueue.ownerPendingLimit || 0) }} 个任务。</template>
                  </p>
                </div>
              </div>
              <span class="text-[11px] text-slate-400">内部队列</span>
            </div>
            <dl v-if="agentQueueMetrics.length" class="mt-3 grid grid-cols-2 border-y border-black/[0.06] dark:border-white/10 sm:grid-cols-4">
              <div v-for="(item, index) in agentQueueMetrics" :key="item.label" class="px-3 py-2.5" :class="index ? 'border-l border-black/[0.06] dark:border-white/10' : ''">
                <dt class="text-[11px] text-slate-500 dark:text-stone-400">{{ item.label }}</dt>
                <dd class="mt-0.5 text-[17px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                  <AnimatedNumber :value="item.value" :formatter="formatNumber" />
                  <template v-if="typeof item.denominator === 'number'">
                    /<AnimatedNumber :value="item.denominator" :formatter="formatNumber" />
                  </template>
                </dd>
              </div>
            </dl>
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
                      运行 <AnimatedNumber :value="user.running_tasks" :formatter="formatNumber" /> · 排队 <AnimatedNumber :value="user.queued_tasks" :formatter="formatNumber" />
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
              <p class="mt-1 text-[13px] text-slate-500">所选日期内的排障指标。</p>
            </div>
            <Gauge class="size-5 text-emerald-600" />
          </div>

          <div class="mt-5 grid gap-2 sm:grid-cols-3">
            <button type="button" class="rounded-xl bg-rose-50 px-3 py-3 text-left transition-colors hover:bg-rose-100 disabled:cursor-default dark:bg-rose-400/10 dark:hover:bg-rose-400/15" :disabled="!totalFailed" @click="openTaskDetails(null, 'error')">
              <div class="text-[11px] font-medium text-rose-700 dark:text-rose-300">失败</div>
              <div class="mt-1 text-lg font-semibold tabular-nums text-rose-700 dark:text-rose-300">
                <AnimatedNumber :value="totalFailed" :formatter="formatNumber" />
              </div>
            </button>
            <div class="rounded-xl bg-amber-50 px-3 py-3 dark:bg-amber-400/10">
              <div class="text-[11px] font-medium text-amber-700 dark:text-amber-300">P95</div>
              <div class="mt-1 text-lg font-semibold tabular-nums text-amber-700 dark:text-amber-300">
                <AnimatedNumber :value="latency?.p95_ms || 0" :formatter="formatDuration" />
              </div>
            </div>
            <div class="rounded-xl bg-slate-100 px-3 py-3 dark:bg-white/[0.06]">
              <div class="text-[11px] font-medium text-slate-500 dark:text-stone-400">最大耗时</div>
              <div class="mt-1 text-lg font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                <AnimatedNumber :value="latency?.max_ms || 0" :formatter="formatDuration" />
              </div>
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
                <span class="text-[13px] font-semibold tabular-nums text-slate-900 dark:text-stone-100">
                  <AnimatedNumber :value="item.value.average_ms" :formatter="formatDuration" />
                </span>
                <span class="w-16 text-right text-[12px] tabular-nums text-slate-500">
                  <AnimatedNumber :value="item.value.p95_ms" :formatter="formatDuration" />
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="grid gap-4 xl:grid-cols-3">
        <div class="studio-card bg-white p-4 dark:bg-[#171a21] xl:col-span-2">
          <div class="flex items-center justify-between gap-4">
            <div>
              <h2 class="text-[20px] font-semibold">{{ sourceLabel() }}数量</h2>
              <p class="mt-1 text-[13px] text-slate-500">统计范围：{{ activeRangeLabel }}。</p>
            </div>
            <span class="rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-xs font-semibold text-[#315be8]">每 15 秒更新</span>
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <button type="button" class="rounded-xl bg-[#F1F5F9] px-4 py-5 text-left transition-colors hover:bg-slate-200 dark:bg-white/[0.05] dark:hover:bg-white/[0.09]" @click="openTaskDetails(null, 'all')">
              <p class="text-xs font-medium text-slate-500">总处理{{ sourceUnit() }}数</p>
              <p class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                <AnimatedNumber :value="totalGenerated" :formatter="formatNumber" />
              </p>
            </button>
            <button type="button" class="rounded-xl bg-emerald-50 px-4 py-5 text-left transition-colors hover:bg-emerald-100 disabled:cursor-default dark:bg-emerald-400/10 dark:hover:bg-emerald-400/15" :disabled="!totalSuccess" @click="openTaskDetails(null, 'success')">
              <p class="text-xs font-medium text-emerald-700 dark:text-emerald-300">成功生成</p>
              <p class="mt-2 text-[30px] font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">
                <AnimatedNumber :value="totalSuccess" :formatter="formatNumber" />
              </p>
            </button>
            <button type="button" class="rounded-xl bg-slate-100 px-4 py-5 text-left transition-colors hover:bg-slate-200 disabled:cursor-default dark:bg-white/[0.06] dark:hover:bg-white/[0.09]" :disabled="!totalCostCount" @click="openTaskDetails(null, 'success')">
              <p class="text-xs font-medium text-slate-500 dark:text-stone-400">总费用</p>
              <p class="mt-2 text-[30px] font-semibold tabular-nums text-slate-950 dark:text-stone-50">
                <AnimatedNumber :value="totalCost" :formatter="formatCost" />
              </p>
              <p class="mt-1 text-[11px] text-slate-400">
                <AnimatedNumber :value="totalCostCount" :formatter="formatNumber" /> 次计费
              </p>
            </button>
            <button type="button" class="rounded-xl bg-rose-50 px-4 py-5 text-left transition-colors hover:bg-rose-100 disabled:cursor-default dark:bg-rose-400/10 dark:hover:bg-rose-400/15" :disabled="!totalFailed" @click="openTaskDetails(null, 'error')">
              <p class="text-xs font-medium text-rose-700 dark:text-rose-300">生成失败</p>
              <p class="mt-2 text-[30px] font-semibold tabular-nums text-rose-700 dark:text-rose-300">
                <AnimatedNumber :value="totalFailed" :formatter="formatNumber" />
              </p>
            </button>
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
            <AnimatedSuccessRing :value="successRate" />
          </div>
        </div>

      </div>

      <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 class="text-[22px] font-semibold">用户{{ sourceLabel() }}统计</h2>
            <p class="mt-1 text-[13px] text-slate-500">成功、失败和合计{{ sourceUnit() }}数按 {{ activeRangeLabel }} 统计；运行与排队为当前实时状态。</p>
          </div>
          <div class="flex w-full flex-col gap-2 xl:w-auto xl:items-end">
            <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div class="relative w-full sm:w-[300px]">
                <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <input
                  v-model="query"
                  class="studio-input h-11 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]"
                  placeholder="搜索用户、姓名或 ID"
                  aria-label="搜索用户、姓名或 ID"
                />
              </div>
              <button type="button" class="studio-button inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-black/[0.06] px-3 text-sm font-medium dark:border-white/10" :disabled="!sortedUsers.length" @click="exportUsersCsv">
                <Download class="size-4" />
                导出 CSV
              </button>
            </div>
            <div class="flex max-w-full overflow-x-auto rounded-xl bg-[#F1F5F9] p-1 dark:bg-white/[0.06]">
              <button
                v-for="scope in userScopes"
                :key="scope.value"
                type="button"
                class="h-8 shrink-0 rounded-lg px-3 text-xs font-medium text-slate-500 transition-colors"
                :class="userScope === scope.value ? 'bg-white text-[#315be8] shadow-sm dark:bg-white/10 dark:text-white' : 'hover:text-slate-900 dark:hover:text-white'"
                :aria-pressed="userScope === scope.value"
                @click="userScope = scope.value"
              >
                {{ scope.label }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="loadError" class="mt-4 flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 sm:flex-row sm:items-center sm:justify-between dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
          <span>监控数据读取失败：{{ loadError }}</span>
          <button type="button" class="studio-button inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white hover:bg-rose-700" @click="load(true)">
            <RefreshCw class="size-3.5" />
            重试
          </button>
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
            <p class="mt-3 text-sm font-semibold">{{ userScope === 'with-output' ? '当前范围没有生成记录' : '暂无匹配监控数据' }}</p>
            <p class="mt-1 text-xs text-slate-500">可以切换“全部用户”或调整日期范围。</p>
          </div>
        </div>

        <template v-else>
          <div class="mt-5 hidden overflow-x-auto pb-1 2xl:block">
            <div class="min-w-[1080px] space-y-2">
            <div class="grid grid-cols-[minmax(220px,1.35fr)_88px_repeat(6,minmax(70px,.58fr))_150px] items-center gap-3 px-4 text-[11px] font-semibold text-slate-400">
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
              v-for="(user, index) in paginatedUsers"
              :key="user.user_id"
              class="studio-row-enter group grid min-h-[82px] grid-cols-[minmax(220px,1.35fr)_88px_repeat(6,minmax(70px,.58fr))_150px] items-center gap-3 rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 py-3 transition-colors duration-200 hover:border-[#4F7CFF]/30 hover:bg-white hover:ring-2 hover:ring-[#4F7CFF]/10 motion-safe:transition-transform motion-safe:hover:-translate-y-0.5 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.07]"
              :style="{ '--studio-row-index': index }"
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
                    <template v-if="isImageSource">
                      <button
                        type="button"
                        class="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-slate-400"
                        :disabled="!user.success_count"
                        :aria-label="`查看${user.name || user.username || '用户'}生成的图片`"
                        title="查看生成图片"
                        @click="openUserImageGallery(user)"
                      >
                        <Images class="size-4" />
                      </button>
                      <button
                        type="button"
                        class="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-slate-400"
                        :disabled="!userHasTaskHistory(user)"
                        :aria-label="`查看${user.name || user.username || '用户'}上传的参考图`"
                        title="查看上传参考图"
                        @click="openUserReferenceGallery(user)"
                      >
                        <CloudUpload class="size-4" />
                      </button>
                    </template>
                    <button
                      v-else
                      type="button"
                      class="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35"
                      :disabled="!user.success_count"
                      :aria-label="`查看${user.name || user.username || '用户'}生成的${sourceUnit()}任务`"
                      :title="`查看生成${sourceUnit()}任务`"
                      @click="openTaskDetails(user, 'success')"
                    >
                      <component :is="isAudioSource ? AudioLines : Video" class="size-4" />
                    </button>
                    <span class="shrink-0 rounded-full bg-white px-2 py-1 text-[11px] text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">
                      {{ roleLabel(user.role) }}
                    </span>
                  </div>
                  <div class="mt-1 flex min-w-0 flex-wrap gap-x-2 text-[10px] text-slate-400">
                    <span v-if="user.last_login_at" class="truncate">登录 {{ user.last_login_at }}</span>
                    <span v-if="user.last_seen_at" class="truncate">活跃 {{ user.last_seen_at }}</span>
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

              <button type="button" class="text-right text-sm font-semibold text-emerald-600 hover:underline disabled:cursor-default disabled:no-underline" :disabled="!user.success_count" @click="openTaskDetails(user, 'success')">
                <AnimatedNumber :value="user.success_count" :formatter="formatNumber" />
              </button>
              <button type="button" class="text-right text-sm font-semibold text-rose-600 hover:underline disabled:cursor-default disabled:no-underline" :disabled="!user.failed_count" @click="openTaskDetails(user, 'error')">
                <AnimatedNumber :value="user.failed_count" :formatter="formatNumber" />
              </button>
              <div class="text-right text-sm font-semibold text-[#4F7CFF]">
                <AnimatedNumber :value="user.running_tasks" :formatter="formatNumber" />
              </div>
              <div class="text-right text-sm font-semibold text-amber-600">
                <AnimatedNumber :value="user.queued_tasks" :formatter="formatNumber" />
              </div>
              <button type="button" class="text-right text-sm font-semibold text-slate-900 hover:underline disabled:cursor-default disabled:no-underline dark:text-stone-100" :disabled="!userVolume(user)" @click="openTaskDetails(user, 'all')">
                <AnimatedNumber :value="userVolume(user)" :formatter="formatNumber" />
              </button>
              <button type="button" class="text-right text-sm font-semibold text-slate-900 hover:underline disabled:cursor-default disabled:no-underline dark:text-stone-100" :disabled="!(user.cost_count || hasCost(user.cost_total))" @click="openTaskDetails(user, 'success')">
                <span class="block tabular-nums"><AnimatedNumber :value="user.cost_total" :formatter="formatCost" /></span>
                <span v-if="user.cost_count" class="block text-[11px] font-normal text-slate-400">
                  <AnimatedNumber :value="user.cost_count" :formatter="formatNumber" /> 次
                </span>
              </button>
              <div>
                <div class="flex items-center justify-between gap-2 text-xs">
                  <span class="font-semibold text-slate-900 dark:text-stone-100">
                    <AnimatedNumber :value="userLoad(user)" :formatter="formatNumber" />
                  </span>
                  <span class="text-slate-500">/ <AnimatedNumber :value="ownerConcurrencyLimit" :formatter="formatNumber" /></span>
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

          <div class="mt-4 space-y-2 2xl:hidden">
            <article
              v-for="(user, index) in paginatedUsers"
              :key="user.user_id"
              class="studio-row-enter rounded-xl border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]"
              :style="{ '--studio-row-index': index }"
            >
              <div class="flex min-h-11 w-full items-center gap-2">
                <button type="button" class="flex min-w-0 flex-1 items-center gap-3 text-left" :aria-expanded="expandedMobileUserId === user.user_id" @click="toggleMobileUser(user)">
                  <div class="flex size-10 shrink-0 items-center justify-center rounded-lg bg-slate-950 text-sm font-semibold text-white dark:bg-white dark:text-slate-950">
                    {{ (user.name || user.username || user.user_id || 'U').slice(0, 1).toUpperCase() }}
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <span class="truncate text-sm font-semibold text-slate-950 dark:text-stone-50">{{ user.name || user.username }}</span>
                      <span class="shrink-0 text-xs" :class="user.online ? 'text-emerald-600' : 'text-slate-400'">{{ user.online ? '在线' : '离线' }}</span>
                    </div>
                  </div>
                  <component :is="expandedMobileUserId === user.user_id ? ChevronUp : ChevronDown" class="size-4 shrink-0 text-slate-400" />
                </button>
                <template v-if="isImageSource">
                  <button
                    type="button"
                    class="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35"
                    :disabled="!user.success_count"
                    :aria-label="`查看${user.name || user.username || '用户'}生成的图片`"
                    title="查看生成图片"
                    @click="openUserImageGallery(user)"
                  >
                    <Images class="size-4" />
                  </button>
                  <button
                    type="button"
                    class="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35"
                    :disabled="!userHasTaskHistory(user)"
                    :aria-label="`查看${user.name || user.username || '用户'}上传的参考图`"
                    title="查看上传参考图"
                    @click="openUserReferenceGallery(user)"
                  >
                    <CloudUpload class="size-4" />
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  class="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-[#4F7CFF]/10 hover:text-[#315be8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 disabled:cursor-default disabled:opacity-35"
                  :disabled="!user.success_count"
                  :aria-label="`查看${user.name || user.username || '用户'}生成的${sourceUnit()}任务`"
                  :title="`查看生成${sourceUnit()}任务`"
                  @click="openTaskDetails(user, 'success')"
                >
                  <component :is="isAudioSource ? AudioLines : Video" class="size-4" />
                </button>
              </div>

              <div class="mt-3 grid grid-cols-2 gap-2 border-t border-black/[0.06] pt-3 text-center dark:border-white/10 sm:grid-cols-5">
                <button type="button" class="min-h-11 rounded-lg bg-emerald-50 px-1 py-2 disabled:cursor-default dark:bg-emerald-400/10" :disabled="!user.success_count" @click="openTaskDetails(user, 'success')">
                  <span class="block text-[11px] text-emerald-700 dark:text-emerald-300">成功</span>
                  <strong class="mt-0.5 block text-sm tabular-nums text-emerald-700 dark:text-emerald-300">
                    <AnimatedNumber :value="user.success_count" :formatter="formatNumber" />
                  </strong>
                </button>
                <button type="button" class="min-h-11 rounded-lg bg-rose-50 px-1 py-2 disabled:cursor-default dark:bg-rose-400/10" :disabled="!user.failed_count" @click="openTaskDetails(user, 'error')">
                  <span class="block text-[11px] text-rose-700 dark:text-rose-300">失败</span>
                  <strong class="mt-0.5 block text-sm tabular-nums text-rose-700 dark:text-rose-300">
                    <AnimatedNumber :value="user.failed_count" :formatter="formatNumber" />
                  </strong>
                </button>
                <button type="button" class="min-h-11 rounded-lg bg-slate-100 px-1 py-2 disabled:cursor-default dark:bg-white/[0.06]" :disabled="!userVolume(user)" @click="openTaskDetails(user, 'all')">
                  <span class="block text-[11px] text-slate-500">合计</span>
                  <strong class="mt-0.5 block text-sm tabular-nums text-slate-900 dark:text-stone-100">
                    <AnimatedNumber :value="userVolume(user)" :formatter="formatNumber" />
                  </strong>
                </button>
                <button type="button" class="min-h-11 rounded-lg bg-slate-100 px-1 py-2 disabled:cursor-default dark:bg-white/[0.06]" :disabled="!(user.cost_count || hasCost(user.cost_total))" @click="openTaskDetails(user, 'success')">
                  <span class="block text-[11px] text-slate-500">费用</span>
                  <strong class="mt-0.5 block text-sm tabular-nums text-slate-900 dark:text-stone-100">
                    <AnimatedNumber :value="user.cost_total" :formatter="formatCost" />
                  </strong>
                </button>
                <div class="min-h-11 rounded-lg bg-[#4F7CFF]/10 px-1 py-2">
                  <span class="block text-[11px] text-[#315be8]">当前任务</span>
                  <strong class="mt-0.5 block text-sm tabular-nums text-[#315be8]">
                    <AnimatedNumber :value="userLoad(user)" :formatter="formatNumber" />
                  </strong>
                </div>
              </div>

              <div class="mt-3 rounded-lg bg-[#4F7CFF]/10 px-3 py-2">
                <div class="flex items-center justify-between gap-3 text-xs">
                  <span class="font-medium text-[#315be8]">当前负载</span>
                  <span class="font-semibold tabular-nums text-[#315be8]">
                    <AnimatedNumber :value="userLoad(user)" :formatter="formatNumber" /> / <AnimatedNumber :value="ownerConcurrencyLimit" :formatter="formatNumber" />
                  </span>
                </div>
                <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-white/[0.08]">
                  <div
                    class="h-full rounded-full bg-[#4F7CFF] transition-[width,background-color] duration-300"
                    :style="{ width: `${userLoadPercent(user)}%` }"
                  />
                </div>
              </div>

              <div v-if="expandedMobileUserId === user.user_id" class="mt-3 grid gap-2 border-t border-black/[0.06] pt-3 text-xs text-slate-500 dark:border-white/10 sm:grid-cols-2">
                <div>角色：<span class="font-medium text-slate-800 dark:text-stone-200">{{ roleLabel(user.role) }}</span></div>
                <div>运行 / 排队：<span class="font-medium tabular-nums text-slate-800 dark:text-stone-200"><AnimatedNumber :value="user.running_tasks" :formatter="formatNumber" /> / <AnimatedNumber :value="user.queued_tasks" :formatter="formatNumber" /></span></div>
                <div>计费次数：<span class="font-medium tabular-nums text-slate-800 dark:text-stone-200"><AnimatedNumber :value="user.cost_count || 0" :formatter="formatNumber" /></span></div>
                <div>平均费用：<span class="font-medium tabular-nums text-slate-800 dark:text-stone-200"><AnimatedNumber :value="user.cost_average" :formatter="formatCost" /></span></div>
                <div>最近登录：<span class="font-medium text-slate-800 dark:text-stone-200">{{ user.last_login_at || '暂无' }}</span></div>
                <div>最近活跃：<span class="font-medium text-slate-800 dark:text-stone-200">{{ user.last_seen_at || '暂无' }}</span></div>
              </div>
            </article>
          </div>

          <div class="mt-4 flex flex-col gap-3 border-t border-black/[0.06] pt-4 text-sm text-slate-500 dark:border-white/10 sm:flex-row sm:items-center sm:justify-between">
            <span>
              显示 {{ formatNumber(userPageStart) }}-{{ formatNumber(userPageEnd) }} / {{ formatNumber(sortedUsers.length) }} 个用户，每页 20 个
            </span>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
                :disabled="userPage <= 1"
                aria-label="上一页"
                @click="goToUserPage(userPage - 1)"
              >
                <ChevronLeft class="size-4" />
              </button>
              <span class="min-w-20 text-center text-sm font-semibold tabular-nums text-slate-900 dark:text-stone-100">
                {{ userPage }} / {{ userTotalPages }}
              </span>
              <button
                type="button"
                class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300"
                :disabled="userPage >= userTotalPages"
                aria-label="下一页"
                @click="goToUserPage(userPage + 1)"
              >
                <ChevronRight class="size-4" />
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="detailPanelOpen" class="fixed inset-0 z-[110] flex justify-end bg-slate-950/35" @mousedown.self="closeTaskDetails">
        <aside class="flex h-full w-full max-w-[680px] flex-col bg-white shadow-[-8px_0_24px_rgba(15,23,42,0.16)] dark:bg-[#171a21]" role="dialog" aria-modal="true" :aria-label="detailTitle()">
          <header class="flex items-start justify-between gap-4 border-b border-black/[0.06] px-4 py-4 sm:px-5 dark:border-white/10">
            <div class="min-w-0">
              <h2 class="truncate text-lg font-semibold text-slate-950 dark:text-stone-50">{{ detailTitle() }}</h2>
              <p class="mt-1 text-sm text-slate-500">统计范围：{{ activeRangeLabel }}</p>
            </div>
            <button type="button" class="studio-button inline-flex size-10 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.08]" aria-label="关闭任务明细" @click="closeTaskDetails">
              <X class="size-4" />
            </button>
          </header>

          <div class="flex-1 overflow-y-auto p-4 sm:p-5">
            <div v-if="detailLoading" class="space-y-2">
              <div v-for="index in 6" :key="index" class="studio-skeleton h-20 rounded-xl" />
            </div>
            <div v-else-if="detailError" class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
              {{ detailError }}
            </div>
            <template v-else-if="selectedDetails">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-[#F8FAFC] px-4 py-3 dark:bg-white/[0.04]">
                <div class="text-sm text-slate-500">
                  共 <strong class="tabular-nums text-slate-950 dark:text-stone-50"><AnimatedNumber :value="selectedDetails.media_count ?? selectedDetails.image_count" :formatter="formatNumber" /></strong> {{ sourceCountUnit() }}，<AnimatedNumber :value="selectedDetails.record_count" :formatter="formatNumber" /> 条记录
                </div>
                <div v-if="selectedDetails.cost_count" class="text-sm text-slate-500">
                  费用 <strong class="tabular-nums text-slate-950 dark:text-stone-50"><AnimatedNumber :value="selectedDetails.cost_total" :formatter="formatCost" /></strong>，<AnimatedNumber :value="selectedDetails.cost_count" :formatter="formatNumber" /> 次计费
                </div>
                <span v-if="monitoringDetailsHasMore(selectedDetails)" class="text-xs text-amber-600">
                  已加载 {{ formatNumber(selectedDetails.items.length) }} / {{ formatNumber(selectedDetails.record_count) }} 条
                </span>
              </div>

              <div v-if="selectedDetails.items.length" class="divide-y divide-black/[0.06] dark:divide-white/10">
                <article v-for="item in selectedDetails.items" :key="item.row_key" class="flex gap-3 py-4 first:pt-0">
                  <button v-if="item.image_url && isImageSource" type="button" class="size-16 shrink-0 overflow-hidden rounded-lg bg-slate-100 text-left transition-colors hover:ring-2 hover:ring-[#4F7CFF]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 dark:bg-white/[0.06]" @click="openGeneratedPreview(item)">
                    <img :src="resolveApiAssetUrl(item.image_url)" alt="生成结果缩略图" class="size-full object-cover" />
                  </button>
                  <a v-else-if="item.video_url" :href="resolveApiAssetUrl(item.video_url)" target="_blank" rel="noreferrer" class="size-16 shrink-0 overflow-hidden rounded-lg bg-slate-100 text-left transition-colors hover:ring-2 hover:ring-[#4F7CFF]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 dark:bg-white/[0.06]" :aria-label="`打开${sourceLabel()}结果`">
                    <video :src="resolveApiAssetUrl(item.video_url)" :poster="resolveApiAssetUrl(item.cover_url)" class="size-full object-cover" muted preload="metadata" />
                  </a>
                  <div v-else-if="item.audio_url" class="grid size-16 shrink-0 place-items-center rounded-lg bg-[#4F7CFF]/10 text-[#315be8] dark:bg-[#4F7CFF]/15 dark:text-[#8CA7FF]">
                    <AudioLines class="size-6" />
                  </div>
                  <div v-else class="grid size-16 shrink-0 place-items-center rounded-lg" :class="item.status === 'error' ? 'bg-rose-50 text-rose-600 dark:bg-rose-400/10' : 'bg-emerald-50 text-emerald-600 dark:bg-emerald-400/10'">
                    <X v-if="item.status === 'error'" class="size-5" />
                    <CheckCircle2 v-else class="size-5" />
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="rounded-md px-2 py-0.5 text-[11px] font-semibold" :class="item.status === 'error' ? 'bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300'">
                        {{ item.status === 'error' ? '失败' : '成功' }}
                      </span>
                      <span class="text-xs text-slate-500">{{ detailOwnerName(item) }}</span>
                      <span class="text-xs tabular-nums text-slate-400"><AnimatedNumber :value="item.media_count ?? item.image_count" :formatter="formatNumber" /> {{ sourceUnit() }}</span>
                      <span v-if="hasCost(item.cost)" class="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700 dark:bg-white/[0.08] dark:text-stone-200">
                        费用 <AnimatedNumber :value="item.cost" :formatter="formatCost" />
                      </span>
                    </div>
                    <p class="mt-1 truncate font-mono text-xs text-slate-700 dark:text-stone-300" :title="item.task_id">{{ item.task_id }}</p>
                    <p v-if="item.error" class="mt-1 line-clamp-2 text-xs leading-5 text-rose-600 dark:text-rose-300">{{ item.error }}</p>
                    <div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
                      <span>{{ item.completed_at || '时间未知' }}</span>
                      <span v-if="item.model">{{ item.model }}</span>
                      <span v-if="item.upstream_task_id" class="max-w-[180px] truncate" :title="item.upstream_task_id">上游 {{ item.upstream_task_id }}</span>
                      <span v-if="item.duration_ms">{{ formatDuration(item.duration_ms) }}</span>
                    </div>
                    <audio
                      v-if="item.audio_url"
                      :src="resolveApiAssetUrl(item.audio_url)"
                      class="mt-3 h-9 w-full max-w-xl"
                      controls
                      preload="none"
                    />
                    <div v-if="taskDetailReferenceItems(item).length" class="mt-3 flex gap-2 overflow-x-auto pb-1">
                      <button
                        v-for="reference in taskDetailReferenceItems(item)"
                        :key="reference.key"
                        type="button"
                        class="group relative size-14 shrink-0 cursor-pointer overflow-hidden rounded-lg border border-black/[0.06] bg-slate-100 transition-colors hover:border-[#4F7CFF]/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 dark:border-white/10 dark:bg-white/[0.06]"
                        :title="reference.label"
                        :aria-label="`预览${reference.label}`"
                        @click="openReferencePreview({ ...reference, taskId: item.task_id, completedAt: item.completed_at })"
                      >
                        <img :src="reference.previewUrl" alt="reference image" class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" decoding="async" />
                        <span v-if="reference.role" class="absolute bottom-0 left-0 right-0 truncate bg-slate-950/70 px-1 py-0.5 text-[9px] leading-none text-white">{{ reference.role }}</span>
                      </button>
                    </div>
                  </div>
                </article>
                <div v-if="monitoringDetailsHasMore(selectedDetails)" class="flex justify-center pt-4">
                  <button
                    type="button"
                    class="studio-button inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-black/[0.08] px-4 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-stone-200"
                    :disabled="detailLoadingMore"
                    @click="loadMoreTaskDetails"
                  >
                    <RefreshCw class="size-4" :class="detailLoadingMore ? 'animate-spin' : ''" />
                    {{ detailLoadingMore ? '加载中' : '加载更多' }}
                  </button>
                </div>
              </div>
              <div v-else class="grid min-h-48 place-items-center text-center text-sm text-slate-500">
                当前筛选范围没有任务明细。
              </div>
            </template>
          </div>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="imageGalleryOpen" class="fixed inset-0 z-[110] flex justify-end bg-slate-950/35" @mousedown.self="closeUserImageGallery">
        <aside class="flex h-full w-full max-w-[860px] flex-col bg-white shadow-[-8px_0_24px_rgba(15,23,42,0.16)] dark:bg-[#171a21]" role="dialog" aria-modal="true" :aria-label="imageGalleryTitle()">
          <header class="flex items-start justify-between gap-4 border-b border-black/[0.06] px-4 py-4 sm:px-5 dark:border-white/10">
            <div class="min-w-0">
              <h2 class="truncate text-lg font-semibold text-slate-950 dark:text-stone-50">{{ imageGalleryTitle() }}</h2>
              <p class="mt-1 text-sm text-slate-500">统计范围：{{ activeRangeLabel }}</p>
            </div>
            <button type="button" class="studio-button inline-flex size-10 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.08]" aria-label="关闭生成图片" @click="closeUserImageGallery">
              <X class="size-4" />
            </button>
          </header>

          <div class="flex-1 overflow-y-auto p-4 sm:p-5">
            <div v-if="imageGalleryLoading" class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
              <div v-for="index in 12" :key="index" class="studio-skeleton aspect-square rounded-xl" />
            </div>
            <div v-else-if="imageGalleryError" class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
              {{ imageGalleryError }}
            </div>
            <template v-else-if="imageGalleryDetails">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-[#F8FAFC] px-4 py-3 dark:bg-white/[0.04]">
                <div class="text-sm text-slate-500">
                  已加载 <strong class="text-slate-950 dark:text-stone-50">{{ formatNumber(imageGalleryItems.length) }}</strong> 张可预览图片
                </div>
                <button
                  type="button"
                  class="text-xs font-semibold text-[#315be8] hover:underline disabled:cursor-default disabled:text-slate-400 disabled:no-underline"
                  :disabled="!imageGalleryUser"
                  @click="imageGalleryUser && openTaskDetails(imageGalleryUser, 'success')"
                >
                  查看任务明细
                </button>
              </div>

              <div v-if="imageGalleryItems.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                <button
                  v-for="item in imageGalleryItems"
                  :key="item.row_key"
                  type="button"
                  class="group block cursor-pointer overflow-hidden rounded-xl border border-black/[0.06] bg-[#F8FAFC] text-left transition-colors hover:border-[#4F7CFF]/35 dark:border-white/10 dark:bg-white/[0.04]"
                  @click="openGeneratedPreview(item)"
                >
                  <div class="aspect-square overflow-hidden bg-slate-100 dark:bg-white/[0.06]">
                    <img :src="item.image_url" alt="生成图片" class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" decoding="async" />
                  </div>
                  <div class="space-y-1 px-3 py-2">
                    <div class="truncate text-[12px] font-medium text-slate-700 dark:text-stone-200" :title="item.task_id">{{ item.task_id }}</div>
                    <div class="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                      <span class="truncate">{{ item.completed_at || '时间未知' }}</span>
                      <span v-if="item.model" class="shrink-0">{{ item.model }}</span>
                    </div>
                  </div>
                </button>
                <button
                  v-if="monitoringDetailsHasMore(imageGalleryDetails)"
                  type="button"
                  class="studio-button col-span-full mx-auto mt-2 inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-black/[0.08] px-4 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-stone-200"
                  :disabled="imageGalleryLoadingMore"
                  @click="loadMoreUserImages"
                >
                  <RefreshCw class="size-4" :class="imageGalleryLoadingMore ? 'animate-spin' : ''" />
                  {{ imageGalleryLoadingMore ? '加载中' : '加载更多图片' }}
                </button>
              </div>
              <div v-else class="grid min-h-56 place-items-center rounded-xl border border-dashed border-slate-300 text-center text-sm text-slate-500 dark:border-white/10">
                当前筛选范围没有可预览的生成图片。
              </div>
            </template>
          </div>
        </aside>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="referenceGalleryOpen" class="fixed inset-0 z-[110] flex justify-end bg-slate-950/35" @mousedown.self="closeUserReferenceGallery">
        <aside class="flex h-full w-full max-w-[860px] flex-col bg-white shadow-[-8px_0_24px_rgba(15,23,42,0.16)] dark:bg-[#171a21]" role="dialog" aria-modal="true" :aria-label="referenceGalleryTitle()">
          <header class="flex items-start justify-between gap-4 border-b border-black/[0.06] px-4 py-4 sm:px-5 dark:border-white/10">
            <div class="min-w-0">
              <h2 class="truncate text-lg font-semibold text-slate-950 dark:text-stone-50">{{ referenceGalleryTitle() }}</h2>
              <p class="mt-1 text-sm text-slate-500">统计范围：{{ activeRangeLabel }}</p>
            </div>
            <button type="button" class="studio-button inline-flex size-10 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.08]" aria-label="关闭上传参考图" @click="closeUserReferenceGallery">
              <X class="size-4" />
            </button>
          </header>

          <div class="flex-1 overflow-y-auto p-4 sm:p-5">
            <div v-if="referenceGalleryLoading" class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
              <div v-for="index in 12" :key="index" class="studio-skeleton aspect-square rounded-xl" />
            </div>
            <div v-else-if="referenceGalleryError" class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300">
              {{ referenceGalleryError }}
            </div>
            <template v-else-if="referenceGalleryDetails">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-[#F8FAFC] px-4 py-3 dark:bg-white/[0.04]">
                <div class="text-sm text-slate-500">
                  已加载 <strong class="text-slate-950 dark:text-stone-50">{{ formatNumber(referenceGalleryItems.length) }}</strong> 张上传参考图，筛选范围共 {{ formatNumber(referenceGalleryDetails.record_count) }} 条任务
                </div>
                <button
                  type="button"
                  class="text-xs font-semibold text-[#315be8] hover:underline disabled:cursor-default disabled:text-slate-400 disabled:no-underline"
                  :disabled="!referenceGalleryUser"
                  @click="referenceGalleryUser && openTaskDetails(referenceGalleryUser, 'all')"
                >
                  查看任务明细
                </button>
              </div>

              <div v-if="referenceGalleryItems.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                <button
                  v-for="item in referenceGalleryItems"
                  :key="item.key"
                  type="button"
                  class="group block overflow-hidden rounded-xl border border-black/[0.06] bg-[#F8FAFC] transition-colors hover:border-[#4F7CFF]/35 dark:border-white/10 dark:bg-white/[0.04]"
                  :aria-label="`预览${item.label}`"
                  @click="openReferencePreview(item)"
                >
                  <div class="aspect-square overflow-hidden bg-slate-100 dark:bg-white/[0.06]">
                    <img :src="item.previewUrl" alt="上传参考图" class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" decoding="async" />
                  </div>
                  <div class="space-y-1 px-3 py-2">
                    <div class="truncate text-[12px] font-medium text-slate-700 dark:text-stone-200" :title="item.label">{{ item.label }}</div>
                    <div class="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                      <span class="truncate" :title="item.taskId">{{ item.taskId }}</span>
                      <span v-if="item.role" class="shrink-0">{{ item.role }}</span>
                    </div>
                    <div class="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                      <span class="truncate">{{ item.completedAt || '时间未知' }}</span>
                      <span v-if="item.model" class="shrink-0">{{ item.model }}</span>
                    </div>
                  </div>
                </button>
                <button
                  v-if="monitoringDetailsHasMore(referenceGalleryDetails)"
                  type="button"
                  class="studio-button col-span-full mx-auto mt-2 inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-black/[0.08] px-4 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-stone-200"
                  :disabled="referenceGalleryLoadingMore"
                  @click="loadMoreUserReferences"
                >
                  <RefreshCw class="size-4" :class="referenceGalleryLoadingMore ? 'animate-spin' : ''" />
                  {{ referenceGalleryLoadingMore ? '加载中' : '加载更多参考图' }}
                </button>
              </div>
              <div v-else class="grid min-h-56 place-items-center rounded-xl border border-dashed border-slate-300 text-center text-sm text-slate-500 dark:border-white/10">
                当前筛选范围没有可预览的上传参考图。
              </div>
            </template>
          </div>
        </aside>
      </div>
    </Teleport>

    <ReferenceImagePreview
      :open="Boolean(referencePreview)"
      :image-url="referencePreview?.previewUrl || ''"
      :title="referencePreview?.label"
      :subtitle="referencePreview?.subtitle"
      @close="referencePreview = null"
    />
  </section>
</template>
