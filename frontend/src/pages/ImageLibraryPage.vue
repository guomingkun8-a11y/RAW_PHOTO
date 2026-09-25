<script setup lang="ts">
import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Check,
  Download,
  ExternalLink,
  Film,
  Heart,
  ImageIcon,
  LoaderCircle,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  WandSparkles,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import ReferenceImagePreview from "@/components/image/ReferenceImagePreview.vue";
import AudioAssetGrid from "@/features/audio-generation/components/AudioAssetGrid.vue";
import { useVideoAssetRefresh } from "@/composables/useVideoTaskPolling";
import {
  bulkDeleteImageLibraryItems,
  downloadImageLibraryItem,
  downloadImageLibraryZip,
  fetchImageLibrary,
  fetchImageLibraryItem,
  fetchPromptTemplates,
  fetchUsers,
  fetchVideoGenerationTasks,
  resolveApiAssetUrl,
  updateImageLibraryItem,
  type ImageLibraryCursor,
  type ImageLibraryItem,
  type PromptTemplate,
  type UserAccount,
  type VideoGenerationTask,
} from "@/lib/api";
import { imageLibraryDisplayPrompt } from "@/lib/prompt-display";
import { sessionState } from "@/stores/session";

const IMAGE_PAGE_SIZE = 20;
const VIDEO_PAGE_SIZE = 9;

const route = useRoute();
const router = useRouter();
const items = ref<ImageLibraryItem[]>([]);
const videoTasks = ref<VideoGenerationTask[]>([]);
const templates = ref<PromptTemplate[]>([]);
const users = ref<UserAccount[]>([]);
const total = ref(0);
const currentPage = ref(1);
const loading = ref(true);
const imageHasMore = ref(false);
const imageCursors = ref<Array<ImageLibraryCursor | null>>([null]);
const videoLoading = ref(false);
const videoRefreshing = ref(false);
const videoTotal = ref<number | undefined>();
const videoHasMore = ref(false);
const videoCursors = ref<string[]>([""]);
const videoError = ref("");
let videoRequestId = 0;
let videoDisposed = false;
let videoRetryPage = 1;
const mediaType = ref<"image" | "video" | "audio">(
  route.query.type === "video" ? "video" : route.query.type === "audio" ? "audio" : "image",
);
const audioRefreshKey = ref(0);
const query = ref(typeof route.query.search === "string" ? route.query.search : "");
const selectedTemplateId = ref<number | null>(null);
const favoriteOnly = ref(false);
const viewScope = ref<"mine" | "all" | "owner">(sessionState.session?.role === "admin" ? "all" : "mine");
const selectedOwnerId = ref("");
const selectedItemId = ref<number | null>(null);
const deleteTarget = ref<ImageLibraryItem | null>(null);
const deletingId = ref<number | null>(null);
const selectedIds = ref<Set<number>>(new Set());
const bulkDeleteOpen = ref(false);
const bulkDeleting = ref(false);
const bulkDownloading = ref(false);
let filterTimer = 0;
let requestId = 0;
let detailRequestId = 0;
const detailLoadingId = ref<number | null>(null);
const referencePreview = ref<{ previewUrl: string; label: string; subtitle: string } | null>(null);

const templateMap = computed(() => new Map(templates.value.map((item) => [item.id, item])));
const ownerMap = computed(() => new Map(users.value.map((item) => [item.id, item])));
const isAdmin = computed(() => sessionState.session?.role === "admin");
const selectedItem = computed(() => items.value.find((item) => item.id === selectedItemId.value) || null);
const selectedItems = computed(() => items.value.filter((item) => selectedIds.value.has(item.id)));
const selectedCount = computed(() => selectedIds.value.size);
const visibleItemIds = computed(() => items.value.map((item) => item.id));
const allVisibleSelected = computed(() => Boolean(items.value.length) && visibleItemIds.value.every((id) => selectedIds.value.has(id)));
const someVisibleSelected = computed(() => visibleItemIds.value.some((id) => selectedIds.value.has(id)));
const completedVideos = computed(() => videoTasks.value.filter((task) => task.status === "success" && videoUrl(task)));
const imageTotalPages = computed(() => Math.max(1, Math.ceil(total.value / IMAGE_PAGE_SIZE)));
const videoTotalPages = computed(() => videoTotal.value === undefined
  ? currentPage.value + (videoHasMore.value ? 1 : 0)
  : Math.max(1, Math.ceil(videoTotal.value / VIDEO_PAGE_SIZE)));
const activeTotal = computed(() => mediaType.value === "video"
  ? videoTotal.value ?? (currentPage.value - 1) * VIDEO_PAGE_SIZE + completedVideos.value.length
  : total.value);
const activePageSize = computed(() => mediaType.value === "video" ? VIDEO_PAGE_SIZE : IMAGE_PAGE_SIZE);
const totalPages = computed(() => mediaType.value === "video" ? videoTotalPages.value : imageTotalPages.value);
const pageStart = computed(() => activeTotal.value ? (currentPage.value - 1) * activePageSize.value + 1 : 0);
const pageEnd = computed(() => mediaType.value === "video"
  ? (currentPage.value - 1) * VIDEO_PAGE_SIZE + completedVideos.value.length
  : Math.min(activeTotal.value, currentPage.value * activePageSize.value));
const paginatedVideos = completedVideos;
const visiblePages = computed(() => {
  const availablePages = mediaType.value === "video" ? videoCursors.value.length : imageCursors.value.length;
  const totalCount = Math.min(totalPages.value, availablePages);
  const current = currentPage.value;
  const start = Math.max(1, Math.min(current - 2, totalCount - 4));
  const end = Math.min(totalCount, start + 4);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
});
const currentLoading = computed(() => mediaType.value === "image"
  ? loading.value
  : mediaType.value === "video" ? videoLoading.value || videoRefreshing.value : false);
const videoAssets = useVideoAssetRefresh(() => videoTasks.value, (updates) => {
  const byId = new Map(updates.map((task) => [task.id, task]));
  videoTasks.value = videoTasks.value.map((task) => byId.get(task.id) || task);
});

async function refreshVideoAsset(task: VideoGenerationTask) {
  try { await videoAssets.refresh(task.id); }
  catch (error) { toast.error(error instanceof Error ? error.message : "刷新视频链接失败"); }
}

function formatFileSize(bytes?: number) {
  if (!bytes) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}
function formatCost(value?: number) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "";
  return `花费 $${Number(value).toFixed(4)}`;
}
function formatCreatedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}
function dimensions(item: ImageLibraryItem) {
  return item.width && item.height ? `${item.width} x ${item.height}` : item.size || "";
}
function thumbnail(item: ImageLibraryItem) {
  return item.thumbnail_url || item.image_url;
}
function displayPrompt(item: ImageLibraryItem | null | undefined) {
  return imageLibraryDisplayPrompt(item);
}
function imageAlt(item: ImageLibraryItem | null | undefined) {
  const prompt = displayPrompt(item);
  return prompt === "未记录 Prompt" ? "生成图片" : prompt;
}
function ownerLabel(ownerId: string) {
  return ownerMap.value.get(ownerId)?.name || ownerMap.value.get(ownerId)?.username || ownerId || "未知用户";
}
function videoUrl(task: VideoGenerationTask) {
  return task.video_url || task.data?.find((item) => item.url)?.url || "";
}
function videoOwnerLabel(task: VideoGenerationTask) {
  return task.owner_name || task.owner_username || ownerLabel(task.owner_id || "");
}
function videoMeta(task: VideoGenerationTask) {
  return [
    task.model,
    task.aspect_ratio,
    task.duration_secs ? `${task.duration_secs}s` : "",
    task.resolution,
    task.storage === "oss" ? "OSS" : task.storage === "local" ? "本地存储" : "",
    task.file_size ? formatFileSize(task.file_size) : "",
  ].filter(Boolean);
}
function formatVideoDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
function openVideo(task: VideoGenerationTask) {
  const url = videoUrl(task);
  if (url) window.open(resolveApiAssetUrl(url), "_blank", "noopener,noreferrer");
}
function referenceItems(item: ImageLibraryItem | null | undefined) {
  return (item?.reference_images || [])
    .map((asset, index) => ({
      key: `${item?.id || "image"}:reference:${index}:${asset.preview_url}`,
      previewUrl: resolveApiAssetUrl(asset.preview_url),
      label: asset.filename || asset.role || `reference-${index + 1}`,
      role: asset.role || "",
    }))
    .filter((asset) => asset.previewUrl);
}
function openReferencePreview(reference: { previewUrl: string; label: string; role?: string }) {
  referencePreview.value = {
    previewUrl: reference.previewUrl,
    label: reference.label || "上传参考图",
    subtitle: reference.role || "",
  };
}
function analysis(item: ImageLibraryItem) {
  const prompt = `${item.prompt || item.revised_prompt || ""}`;
  if (prompt.includes("详情") || prompt.toLowerCase().includes("detail")) return "适合详情页首屏，建议继续强化痛点标题、功能分区和信任背书。";
  if (prompt.includes("白底") || item.size === "1024x1024") return "适合作为商品主图或平台首图，主体清晰，建议检查边缘和包装文字。";
  if (prompt.includes("小红书") || prompt.toLowerCase().includes("tiktok")) return "适合社媒封面，建议保留顶部标题空间并输出竖版变体。";
  return "画面可作为商业视觉资产复用，建议根据平台规格继续生成一组同风格变体。";
}

async function load(page = currentPage.value) {
  const nextPage = Math.max(1, Math.floor(page));
  const cursor = nextPage === 1 ? null : imageCursors.value[nextPage - 1];
  if (cursor === undefined) return;
  const currentId = ++requestId;
  if (nextPage === 1) imageCursors.value = [null];
  loading.value = true;
  try {
    const data = await fetchImageLibrary({
      limit: IMAGE_PAGE_SIZE,
      cursor,
      q: query.value.trim(),
      productId: 0,
      templateId: selectedTemplateId.value || 0,
      favorite: favoriteOnly.value,
      allOwners: isAdmin.value && (viewScope.value === "all" || viewScope.value === "owner"),
      ownerId: isAdmin.value && viewScope.value === "owner" ? selectedOwnerId.value : "",
    });
    if (currentId !== requestId) return;
    const maxPage = Math.max(1, Math.ceil(data.total / IMAGE_PAGE_SIZE));
    if (data.total > 0 && nextPage > maxPage) {
      currentPage.value = maxPage;
      await load(maxPage);
      return;
    }
    items.value = data.items;
    total.value = data.total;
    imageHasMore.value = Boolean(data.has_more && data.next_cursor);
    imageCursors.value = imageCursors.value.slice(0, nextPage);
    if (imageHasMore.value && data.next_cursor) imageCursors.value[nextPage] = data.next_cursor;
    currentPage.value = nextPage;
    const visibleIds = new Set(data.items.map((item) => item.id));
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => visibleIds.has(id)));
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取历史图库失败");
  } finally {
    if (currentId === requestId) loading.value = false;
  }
}

async function openImageDetails(item: ImageLibraryItem) {
  selectedItemId.value = item.id;
  if (item.reference_images !== undefined) return;
  const currentId = ++detailRequestId;
  detailLoadingId.value = item.id;
  try {
    const detail = await fetchImageLibraryItem(item.id, { includeReferences: true });
    if (currentId !== detailRequestId) return;
    items.value = items.value.map((current) => current.id === detail.id ? detail : current);
  } catch (error) {
    if (currentId === detailRequestId) {
      toast.error(error instanceof Error ? error.message : "读取图片详情失败");
    }
  } finally {
    if (currentId === detailRequestId) detailLoadingId.value = null;
  }
}

async function loadVideos(showSpinner = true, page = 1) {
  const cursor = page === 1 ? "" : videoCursors.value[page - 1];
  if (cursor === undefined) return;
  const currentId = ++videoRequestId;
  const scope = JSON.stringify([query.value, viewScope.value, selectedOwnerId.value]);
  videoRetryPage = page;
  if (showSpinner) videoLoading.value = true;
  else videoRefreshing.value = true;
  videoError.value = "";
  try {
    const data = await fetchVideoGenerationTasks([], {
      limit: VIDEO_PAGE_SIZE,
      cursor,
      status: "success",
      q: query.value,
      allOwners: isAdmin.value && (viewScope.value === "all" || viewScope.value === "owner"),
      ownerId: isAdmin.value && viewScope.value === "owner" ? selectedOwnerId.value : "",
    });
    if (videoDisposed || currentId !== videoRequestId || mediaType.value !== "video"
      || scope !== JSON.stringify([query.value, viewScope.value, selectedOwnerId.value])) return;
    videoTasks.value = data.items;
    videoTotal.value = data.total;
    videoHasMore.value = Boolean(data.has_more && data.next_cursor && data.next_cursor !== cursor);
    videoCursors.value = videoCursors.value.slice(0, page);
    if (videoHasMore.value) videoCursors.value[page] = data.next_cursor!;
    currentPage.value = page;
  } catch (error) {
    if (currentId !== videoRequestId || videoDisposed) return;
    videoError.value = error instanceof Error ? error.message : "读取历史视频失败";
  } finally {
    if (currentId === videoRequestId) {
      videoLoading.value = false;
      videoRefreshing.value = false;
    }
  }
}

function switchMediaType(value: "image" | "video" | "audio") {
  if (mediaType.value === value) return;
  mediaType.value = value;
  currentPage.value = 1;
  clearSelection();
  selectedItemId.value = null;
  void router.replace({
    query: {
      ...route.query,
      type: value === "image" ? undefined : value,
    },
  });
  if (value === "video") void loadVideos();
  else if (value === "image") void load(1);
}

function refreshCurrent() {
  if (mediaType.value === "video") void loadVideos(false);
  else if (mediaType.value === "image") void load(currentPage.value);
  else audioRefreshKey.value += 1;
}

async function loadUsers() {
  try {
    users.value = (await fetchUsers()).items;
    if (viewScope.value === "owner" && !selectedOwnerId.value && users.value.length) {
      selectedOwnerId.value = users.value[0].id;
    }
  } catch {
    users.value = [];
  }
}

function goToPage(page: number) {
  if (currentLoading.value || page < 1 || page > totalPages.value || page === currentPage.value) return;
  if (mediaType.value === "video") {
    void loadVideos(false, page);
    return;
  }
  void load(page);
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function replaceSelectedIds(ids: number[]) {
  selectedIds.value = new Set(ids);
}

function toggleSelected(item: ImageLibraryItem) {
  const next = new Set(selectedIds.value);
  if (next.has(item.id)) next.delete(item.id);
  else next.add(item.id);
  selectedIds.value = next;
}

function toggleVisibleSelected() {
  const next = new Set(selectedIds.value);
  if (allVisibleSelected.value) {
    visibleItemIds.value.forEach((id) => next.delete(id));
  } else {
    visibleItemIds.value.forEach((id) => next.add(id));
  }
  selectedIds.value = next;
}

function clearSelection() {
  replaceSelectedIds([]);
}

async function download(item: ImageLibraryItem) {
  try {
    const { blob, filename } = await downloadImageLibraryItem(item.id);
    saveBlob(blob, filename || `image-${item.id}.png`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "下载图片失败");
  }
}

async function downloadSelected() {
  if (!selectedCount.value || bulkDownloading.value) return;
  bulkDownloading.value = true;
  try {
    const blob = await downloadImageLibraryZip({
      ids: Array.from(selectedIds.value),
      folderName: `历史图库-${new Date().toISOString().slice(0, 10)}`,
    });
    saveBlob(blob, `历史图库-${selectedCount.value}张.zip`);
    toast.success(`已打包 ${selectedCount.value} 张图片`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "批量下载失败");
  } finally {
    bulkDownloading.value = false;
  }
}

async function favorite(item: ImageLibraryItem) {
  try {
    const value = !item.favorite;
    await updateImageLibraryItem(item.id, { favorite: value });
    item.favorite = value;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "更新收藏失败");
  }
}
function requestRemove(item: ImageLibraryItem) {
  deleteTarget.value = item;
}
async function confirmRemove() {
  if (!deleteTarget.value || deletingId.value) return;
  await remove(deleteTarget.value);
}

function requestBulkRemove() {
  if (!selectedCount.value || bulkDeleting.value) return;
  bulkDeleteOpen.value = true;
}

async function confirmBulkRemove() {
  if (!selectedCount.value || bulkDeleting.value) return;
  bulkDeleting.value = true;
  const ids = Array.from(selectedIds.value);
  try {
    const result = await bulkDeleteImageLibraryItems(ids);
    bulkDeleteOpen.value = false;
    const deletedIds = new Set(ids);
    if (selectedItemId.value && deletedIds.has(selectedItemId.value)) selectedItemId.value = null;
    clearSelection();
    toast.success(`已移出 ${result.deleted} 张图片`);
    await load(currentPage.value);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "批量删除失败");
  } finally {
    bulkDeleting.value = false;
  }
}

async function remove(item: ImageLibraryItem) {
  deletingId.value = item.id;
  try {
    await updateImageLibraryItem(item.id, { deleted: true });
    if (selectedItemId.value === item.id) selectedItemId.value = null;
    if (deleteTarget.value?.id === item.id) deleteTarget.value = null;
    const next = new Set(selectedIds.value);
    next.delete(item.id);
    selectedIds.value = next;
    toast.success("图片已移出图库");
    await load(currentPage.value);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除图片失败");
  } finally {
    if (deletingId.value === item.id) deletingId.value = null;
  }
}
function globalSearch(event: Event) {
  query.value = event instanceof CustomEvent ? String(event.detail?.query || "") : "";
  currentPage.value = 1;
}

watch([query, selectedTemplateId, favoriteOnly], () => {
  clearSelection();
  currentPage.value = 1;
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => {
    if (mediaType.value === "video") void loadVideos();
    else if (mediaType.value === "image") void load(1);
  }, 350);
});
watch([viewScope, selectedOwnerId], () => {
  if (!isAdmin.value) return;
  if (viewScope.value === "owner" && !selectedOwnerId.value && users.value.length) {
    selectedOwnerId.value = users.value[0].id;
    return;
  }
  clearSelection();
  currentPage.value = 1;
  if (mediaType.value === "video") void loadVideos();
  else if (mediaType.value === "image") void load(1);
});
watch(() => route.query.type, (value) => {
  const next = value === "video" ? "video" : value === "audio" ? "audio" : "image";
  if (next === mediaType.value) return;
  mediaType.value = next;
  currentPage.value = 1;
  clearSelection();
  selectedItemId.value = null;
  if (next === "video") void loadVideos();
  else if (next === "image") void load(1);
});

onMounted(async () => {
  window.addEventListener("image-library-search", globalSearch);
  const templateRequest = fetchPromptTemplates()
    .then((response) => { templates.value = response.items; })
    .catch(() => { templates.value = []; });
  const userRequest = isAdmin.value ? loadUsers() : Promise.resolve();
  const recordRequest = mediaType.value === "video" ? loadVideos() : mediaType.value === "image" ? load(1) : Promise.resolve();
  await Promise.all([templateRequest, userRequest, recordRequest]);
});
onBeforeUnmount(() => {
  videoDisposed = true;
  videoRequestId += 1;
  detailRequestId += 1;
  window.clearTimeout(filterTimer);
  window.removeEventListener("image-library-search", globalSearch);
});
</script>

<template>
  <section class="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <div class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div class="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">Asset Gallery</div>
            <h1 class="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">历史</h1>
            <p class="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              集中查看你的图片、视频和音频生成结果，通过上方选项分开浏览。
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <div class="inline-flex rounded-xl border border-black/[0.06] bg-slate-100 p-1 dark:border-white/10 dark:bg-white/[0.05]" role="tablist" aria-label="历史类型">
              <button type="button" role="tab" :aria-selected="mediaType === 'image'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="mediaType === 'image' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchMediaType('image')">
                <ImageIcon class="size-4" />
                图片
              </button>
              <button type="button" role="tab" :aria-selected="mediaType === 'video'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="mediaType === 'video' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchMediaType('video')">
                <Film class="size-4" />
                视频
              </button>
              <button type="button" role="tab" :aria-selected="mediaType === 'audio'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="mediaType === 'audio' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchMediaType('audio')">
                <AudioLines class="size-4" />
                音频
              </button>
            </div>
            <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]" :disabled="currentLoading" @click="refreshCurrent">
              <LoaderCircle v-if="currentLoading" class="size-4 animate-spin" />
              <RefreshCw v-else class="size-4" />
              刷新
            </button>
          </div>
        </div>
        <div class="mt-5 grid gap-2 xl:grid-cols-[minmax(240px,520px)_190px_auto_auto]">
          <div class="relative">
            <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" :placeholder="mediaType === 'image' ? '搜索用户提示词、模型或优化后的提示词' : '搜索提示词、模型、用户或任务 ID'" data-testid="library-search-input" />
          </div>
          <select v-if="mediaType === 'image'" v-model="selectedTemplateId" class="studio-input h-12 px-3">
            <option :value="null">全部模板</option>
            <option v-for="template in templates" :key="template.id" :value="template.id">{{ template.name }}</option>
          </select>
          <select v-if="isAdmin" v-model="viewScope" class="studio-input h-12 px-3">
            <option value="mine">我的{{ mediaType === 'video' ? '视频' : mediaType === 'audio' ? '音频' : '图片' }}</option>
            <option value="all">全部用户</option>
            <option value="owner">指定用户</option>
          </select>
          <select v-if="isAdmin && viewScope === 'owner'" v-model="selectedOwnerId" class="studio-input h-12 px-3">
            <option value="">选择用户</option>
            <option v-for="user in users" :key="user.id" :value="user.id">{{ user.name || user.username || user.id }}</option>
          </select>
          <label v-if="mediaType === 'image'" class="studio-button inline-flex h-12 w-fit cursor-pointer items-center gap-2 rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 text-sm dark:border-white/10 dark:bg-white/[0.04]">
            <input v-model="favoriteOnly" type="checkbox" class="size-4 accent-[#4F7CFF]" />
            只看收藏
          </label>
        </div>
      </div>

      <div v-if="mediaType === 'image' && loading && !items.length" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
        <div v-for="index in 12" :key="index" class="studio-skeleton h-[330px] rounded-[20px]" />
      </div>

      <div v-else-if="mediaType === 'image' && !items.length" class="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
        <div>
          <div class="mx-auto flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
            <ImageIcon class="size-5" />
          </div>
          <h2 class="mt-4 text-lg font-semibold">暂无图片资产</h2>
          <p class="mt-1 text-sm text-slate-500">在图片工作台完成生成后，结果会自动出现在这里。</p>
        </div>
      </div>

      <template v-else-if="mediaType === 'image'">
        <div class="studio-card flex flex-col gap-3 bg-white px-4 py-3 dark:bg-[#171a21] md:flex-row md:items-center md:justify-between">
          <label class="inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl border border-black/[0.06] bg-[#F8FAFC] px-3 text-sm font-medium text-slate-700 dark:border-white/10 dark:bg-white/[0.05] dark:text-stone-200">
            <input type="checkbox" class="size-4 accent-[#4F7CFF]" :checked="allVisibleSelected" :aria-checked="someVisibleSelected && !allVisibleSelected ? 'mixed' : allVisibleSelected" data-testid="library-select-visible" @change="toggleVisibleSelected" />
            全选当前页
          </label>
          <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
            <span class="text-sm text-slate-500 dark:text-stone-400">已选择 {{ selectedCount }} 张</span>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="studio-button inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-black/[0.08] px-3 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10 dark:text-stone-200" :disabled="!selectedCount || bulkDownloading" data-testid="library-bulk-download" @click="downloadSelected">
                <LoaderCircle v-if="bulkDownloading" class="size-4 animate-spin" />
                <Download v-else class="size-4" />
                批量下载
              </button>
              <button type="button" class="studio-button inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-rose-200 px-3 text-sm font-semibold text-rose-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-rose-500/30" :disabled="!selectedCount || bulkDeleting" data-testid="library-bulk-delete" @click="requestBulkRemove">
                <Trash2 class="size-4" />
                批量删除
              </button>
              <button type="button" class="studio-button inline-flex h-10 items-center justify-center rounded-xl px-3 text-sm text-slate-500 disabled:cursor-not-allowed disabled:opacity-45" :disabled="!selectedCount || bulkDeleting || bulkDownloading" @click="clearSelection">
                取消选择
              </button>
            </div>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
          <article v-for="item in items" :key="item.id" class="group studio-card flex min-h-[330px] flex-col overflow-hidden bg-white dark:bg-[#171a21]" :class="selectedIds.has(item.id) ? 'border-[#4F7CFF]/45 ring-2 ring-[#4F7CFF]/30' : ''" data-testid="library-image-card">
            <div class="relative">
              <button type="button" class="block aspect-[4/3] w-full overflow-hidden bg-slate-100 text-left dark:bg-white/[0.04]" @click="openImageDetails(item)">
                <img :src="thumbnail(item)" :alt="imageAlt(item)" class="h-full w-full object-cover transition duration-300 group-hover:scale-[1.01]" loading="lazy" decoding="async" />
              </button>
              <button type="button" class="studio-button absolute left-3 top-3 inline-flex size-9 items-center justify-center rounded-xl border text-white shadow-sm" :class="selectedIds.has(item.id) ? 'border-[#4F7CFF] bg-[#4F7CFF]' : 'border-white/70 bg-slate-950/45 hover:bg-slate-950/70'" :aria-label="selectedIds.has(item.id) ? '取消选择图片' : '选择图片'" @click.stop="toggleSelected(item)">
                <Check v-if="selectedIds.has(item.id)" class="size-4" />
              </button>
            </div>
            <div class="flex min-h-0 flex-1 flex-col p-3">
              <div class="flex items-center justify-between gap-2">
                <div class="flex min-w-0 flex-wrap gap-1.5">
                  <span class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[11px] font-semibold text-[#315be8]">{{ item.mode === 'edit' ? '图生图' : '文生图' }}</span>
                  <span class="max-w-full truncate rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-500 dark:bg-white/[0.08]">{{ dimensions(item) || item.model }}</span>
                </div>
                <button type="button" class="studio-button inline-flex size-8 shrink-0 items-center justify-center rounded-xl" :class="item.favorite ? 'text-rose-500' : 'text-slate-400 hover:bg-rose-50 hover:text-rose-500'" aria-label="收藏" @click="favorite(item)">
                  <Heart class="size-4" :fill="item.favorite ? 'currentColor' : 'none'" />
                </button>
              </div>
              <p class="mt-3 line-clamp-2 text-sm leading-6 text-slate-700 dark:text-stone-200">{{ displayPrompt(item) }}</p>
              <div class="mt-3 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                <span>{{ formatCreatedAt(item.created_at) }}</span>
                <span>{{ formatFileSize(item.file_size) }}</span>
              </div>
              <div class="mt-1 truncate text-[11px] text-slate-400">{{ ownerLabel(item.owner_id) }}</div>
              <div class="mt-auto flex gap-1 border-t border-black/[0.06] pt-3 dark:border-white/10">
                <button type="button" class="studio-button inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl hover:bg-slate-100 dark:hover:bg-white/[0.08]" @click="download(item)">
                  <Download class="size-3.5" />
                  下载
                </button>
                <button type="button" class="studio-button inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl text-rose-600 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="deletingId === item.id" @click="requestRemove(item)">
                  <Trash2 class="size-3.5" />
                  删除
                </button>
              </div>
            </div>
          </article>
        </div>
      </template>

      <template v-else-if="mediaType === 'video'">
        <div v-if="videoError" role="alert" class="flex items-center justify-between gap-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-400/10 dark:text-rose-300">
          <span>{{ videoError }}</span>
          <button type="button" class="studio-button shrink-0 p-2" title="重试加载视频" aria-label="重试加载视频" :disabled="currentLoading" @click="loadVideos(false, videoRetryPage)"><RefreshCw class="size-4" /></button>
        </div>
        <div v-if="videoLoading && !completedVideos.length" class="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <div v-for="index in VIDEO_PAGE_SIZE" :key="index" class="studio-skeleton aspect-video rounded-2xl" />
        </div>

        <div v-else-if="!completedVideos.length && !videoError" class="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
          <div>
            <div class="mx-auto flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
              <Film class="size-5" />
            </div>
            <h2 class="mt-4 text-lg font-semibold">{{ query ? "没有匹配的视频" : "暂无视频资产" }}</h2>
            <p class="mt-1 text-sm text-slate-500">{{ query ? "换一个关键词试试。" : "完成视频生成后，结果会自动出现在这里。" }}</p>
          </div>
        </div>

        <div v-else class="grid items-stretch gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <article v-for="task in paginatedVideos" :key="task.id" class="studio-card flex h-full min-h-[390px] flex-col overflow-hidden bg-white dark:bg-[#171a21]" data-testid="library-video-card">
            <div class="aspect-video w-full shrink-0 overflow-hidden bg-slate-950">
              <video class="h-full w-full object-cover object-center" :src="resolveApiAssetUrl(videoUrl(task))" :poster="resolveApiAssetUrl(task.cover_url || '')" controls playsinline preload="metadata" @error="videoAssets.onMediaError(task.id)" />
            </div>
            <div class="flex min-h-0 flex-1 flex-col p-3">
              <div class="flex h-[52px] min-w-0 flex-wrap content-start gap-1.5 overflow-hidden">
                <span class="inline-flex items-center gap-1 rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[11px] font-semibold text-[#315be8]">
                  <Film class="size-3" />
                  视频
                </span>
                <span v-for="item in videoMeta(task)" :key="`${task.id}-${item}`" class="max-w-full truncate rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-500 dark:bg-white/[0.08] dark:text-stone-300">{{ item }}</span>
              </div>
              <p class="mt-3 line-clamp-3 min-h-[72px] text-sm leading-6 text-slate-700 dark:text-stone-200">{{ task.prompt || "未记录提示词" }}</p>
              <div class="mt-3 flex items-center justify-between gap-2 text-[11px] text-slate-500 dark:text-stone-400">
                <span>{{ formatVideoDate(task.created_at) }}</span>
                <span v-if="formatCost(task.cost)" class="font-semibold text-[#315be8]">{{ formatCost(task.cost) }}</span>
              </div>
              <div class="mt-1 truncate text-[11px] text-slate-400" :title="videoOwnerLabel(task)">用户：{{ videoOwnerLabel(task) }}</div>
              <div class="mt-auto flex items-center justify-between gap-2 border-t border-black/[0.06] pt-3 dark:border-white/10">
                <span v-if="task.storage_error" class="truncate text-[11px] text-amber-600" :title="task.storage_error">存储回退</span>
                <span v-else class="text-[11px] text-slate-400">已完成</span>
                <div class="flex items-center gap-1.5">
                  <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-lg" title="刷新视频链接" aria-label="刷新视频链接" @click="refreshVideoAsset(task)"><RefreshCw class="size-4" /></button>
                  <button type="button" class="studio-button inline-flex size-9 items-center justify-center rounded-xl" title="打开视频" aria-label="打开视频" @click="openVideo(task)">
                    <ExternalLink class="size-4" />
                  </button>
                  <a class="studio-button inline-flex size-9 items-center justify-center rounded-xl" :href="resolveApiAssetUrl(videoUrl(task))" :download="`video-${task.id}.mp4`" title="下载视频" aria-label="下载视频">
                    <Download class="size-4" />
                  </a>
                </div>
              </div>
            </div>
          </article>
        </div>
      </template>

      <AudioAssetGrid
        v-else
        :key="audioRefreshKey"
        :active="mediaType === 'audio'"
        :query="query"
        :all-owners="isAdmin && (viewScope === 'all' || viewScope === 'owner')"
        :owner-id="isAdmin && viewScope === 'owner' ? selectedOwnerId : ''"
      />

      <div v-if="(mediaType === 'image' && items.length) || (mediaType === 'video' && completedVideos.length)" class="studio-card flex flex-col gap-3 bg-white px-4 py-3 dark:bg-[#171a21] sm:flex-row sm:items-center sm:justify-between">
        <div class="text-sm text-slate-500 dark:text-stone-400">
          <template v-if="mediaType === 'video' && videoTotal === undefined">第 {{ currentPage }} 页，显示 {{ pageStart }}-{{ pageEnd }} 条视频，每页 {{ VIDEO_PAGE_SIZE }} 条</template>
          <template v-else>第 {{ currentPage }} / {{ totalPages }} 页，显示 {{ pageStart }}-{{ pageEnd }} / {{ activeTotal }} {{ mediaType === "video" ? "条视频" : "张图片" }}，每页 {{ activePageSize }} 条</template>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300" :disabled="currentLoading || currentPage <= 1" aria-label="上一页" @click="goToPage(currentPage - 1)">
            <ChevronLeft class="size-4" />
          </button>
          <button v-for="page in visiblePages" :key="page" type="button" class="studio-button inline-flex h-10 min-w-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold" :class="page === currentPage ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:text-stone-300'" :disabled="currentLoading" :aria-current="page === currentPage ? 'page' : undefined" @click="goToPage(page)">
            {{ page }}
          </button>
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300" :disabled="currentLoading || (mediaType === 'video' ? !videoHasMore : !imageHasMore)" aria-label="下一页" @click="goToPage(currentPage + 1)">
            <ChevronRight class="size-4" />
          </button>
        </div>
      </div>
    </div>
  </section>

  <BaseModal :open="Boolean(selectedItem)" title="图片详情" width-class="max-w-[980px]" @close="selectedItemId = null">
    <div v-if="selectedItem" class="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(300px,.8fr)]">
      <div class="overflow-hidden rounded-2xl bg-slate-100 dark:bg-white/[0.04]">
        <img :src="selectedItem.image_url" :alt="imageAlt(selectedItem)" class="h-auto max-h-[72dvh] w-full object-contain" loading="eager" decoding="async" />
      </div>
      <div class="space-y-4">
        <div>
          <h3 class="text-sm font-semibold">用户提示词</h3>
          <p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600 dark:text-stone-300">{{ displayPrompt(selectedItem) }}</p>
        </div>
        <div v-if="detailLoadingId === selectedItem.id" class="flex items-center gap-2 text-sm text-slate-500">
          <LoaderCircle class="size-4 animate-spin" />
          正在读取参考图
        </div>
        <div v-else-if="referenceItems(selectedItem).length">
          <h3 class="text-sm font-semibold">上传参考图</h3>
          <div class="mt-2 flex gap-2 overflow-x-auto pb-1">
            <button
              v-for="reference in referenceItems(selectedItem)"
              :key="reference.key"
              type="button"
              class="group relative size-16 shrink-0 cursor-pointer overflow-hidden rounded-xl border border-black/[0.06] bg-slate-100 transition-colors hover:border-[#4F7CFF]/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/35 dark:border-white/10 dark:bg-white/[0.06]"
              :title="reference.label"
              :aria-label="`预览${reference.label}`"
              @click="openReferencePreview(reference)"
            >
              <img :src="reference.previewUrl" alt="上传参考图" class="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]" loading="lazy" decoding="async" />
              <span v-if="reference.role" class="absolute bottom-0 left-0 right-0 truncate bg-slate-950/70 px-1 py-0.5 text-[9px] leading-none text-white">{{ reference.role }}</span>
            </button>
          </div>
        </div>
        <div class="rounded-2xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/[0.06] p-4">
          <div class="flex items-center gap-2 text-sm font-semibold text-[#315be8]">
            <Sparkles class="size-4" />
            AI 资产分析
          </div>
          <p class="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">{{ analysis(selectedItem) }}</p>
        </div>
        <dl class="grid grid-cols-2 gap-2 text-xs">
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]"><dt class="text-slate-500">模型</dt><dd class="mt-1 font-semibold">{{ selectedItem.model || '默认' }}</dd></div>
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]"><dt class="text-slate-500">尺寸</dt><dd class="mt-1 font-semibold">{{ dimensions(selectedItem) || '未知' }}</dd></div>
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]"><dt class="text-slate-500">类型</dt><dd class="mt-1 truncate font-semibold">{{ selectedItem.mode === 'edit' ? '图生图' : '文生图' }}</dd></div>
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]"><dt class="text-slate-500">模板</dt><dd class="mt-1 truncate font-semibold">{{ selectedItem.template_id ? templateMap.get(selectedItem.template_id)?.name || selectedItem.template_id : '未绑定' }}</dd></div>
          <div class="rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]"><dt class="text-slate-500">用户</dt><dd class="mt-1 truncate font-semibold">{{ ownerLabel(selectedItem.owner_id) }}</dd></div>
        </dl>
        <div class="flex gap-2">
          <button type="button" class="studio-button inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-slate-950 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" @click="download(selectedItem)">
            <Download class="size-4" />
            下载
          </button>
          <RouterLink to="/image" class="studio-button inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl border border-black/[0.08] text-sm font-semibold dark:border-white/10">
            <WandSparkles class="size-4" />
            继续创作
          </RouterLink>
        </div>
      </div>
    </div>
  </BaseModal>

  <ReferenceImagePreview
    :open="Boolean(referencePreview)"
    :image-url="referencePreview?.previewUrl || ''"
    :title="referencePreview?.label"
    :subtitle="referencePreview?.subtitle"
    @close="referencePreview = null"
  />

  <BaseModal :open="Boolean(deleteTarget)" title="确认删除图片" description="删除后这张图片会从历史图库移出，后续不会在图库列表中展示。" width-class="max-w-[460px]" :show-close="false" @close="deleteTarget = null">
    <div v-if="deleteTarget" class="space-y-4 p-5">
      <div class="flex gap-3 rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]">
        <img :src="thumbnail(deleteTarget)" :alt="imageAlt(deleteTarget)" class="size-16 shrink-0 rounded-lg object-cover" loading="lazy" decoding="async" />
        <div class="min-w-0 text-sm">
          <p class="line-clamp-2 leading-6 text-slate-700 dark:text-stone-200">{{ displayPrompt(deleteTarget) }}</p>
          <p class="mt-1 text-xs text-slate-500">{{ formatCreatedAt(deleteTarget.created_at) }} · {{ ownerLabel(deleteTarget.owner_id) }}</p>
        </div>
      </div>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" :disabled="Boolean(deletingId)" @click="deleteTarget = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-70" :disabled="Boolean(deletingId)" @click="confirmRemove">
          <LoaderCircle v-if="deletingId === deleteTarget.id" class="size-4 animate-spin" />
          确认删除
        </button>
      </div>
    </div>
  </BaseModal>

  <BaseModal :open="bulkDeleteOpen" title="确认批量删除" description="删除后这些图片会从历史图库移出，后续不会在图库列表中展示。" width-class="max-w-[520px]" :show-close="false" @close="bulkDeleteOpen = false">
    <div class="space-y-4 p-5">
      <div class="rounded-xl bg-slate-100 p-4 text-sm leading-6 text-slate-700 dark:bg-white/[0.06] dark:text-stone-200">
        将移出当前选中的 {{ selectedCount }} 张图片。生成记录和源文件不会被物理删除，只是不再显示在历史图库中。
      </div>
      <div v-if="selectedItems.length" class="grid grid-cols-6 gap-2">
        <img v-for="item in selectedItems.slice(0, 12)" :key="item.id" :src="thumbnail(item)" :alt="imageAlt(item)" class="aspect-square rounded-lg object-cover" loading="lazy" decoding="async" />
      </div>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" :disabled="bulkDeleting" @click="bulkDeleteOpen = false">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-70" :disabled="bulkDeleting || !selectedCount" data-testid="library-bulk-delete-confirm" @click="confirmBulkRemove">
          <LoaderCircle v-if="bulkDeleting" class="size-4 animate-spin" />
          确认删除
        </button>
      </div>
    </div>
  </BaseModal>
</template>
