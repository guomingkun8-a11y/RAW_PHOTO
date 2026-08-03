<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
  Check,
  Download,
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
import { useRoute } from "vue-router";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import {
  bulkDeleteImageLibraryItems,
  downloadImageLibraryZip,
  fetchImageLibrary,
  fetchPromptTemplates,
  fetchUsers,
  updateImageLibraryItem,
  type ImageLibraryItem,
  type PromptTemplate,
  type UserAccount,
} from "@/lib/api";
import { sessionState } from "@/stores/session";

const PAGE_SIZE = 20;

const route = useRoute();
const items = ref<ImageLibraryItem[]>([]);
const templates = ref<PromptTemplate[]>([]);
const users = ref<UserAccount[]>([]);
const total = ref(0);
const currentPage = ref(1);
const loading = ref(true);
const query = ref("");
const selectedTemplateId = ref<number | null>(null);
const favoriteOnly = ref(false);
const viewScope = ref<"mine" | "all" | "owner">("mine");
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

const templateMap = computed(() => new Map(templates.value.map((item) => [item.id, item])));
const ownerMap = computed(() => new Map(users.value.map((item) => [item.id, item])));
const isAdmin = computed(() => sessionState.session?.role === "admin");
const selectedItem = computed(() => items.value.find((item) => item.id === selectedItemId.value) || null);
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));
const pageStart = computed(() => (total.value ? (currentPage.value - 1) * PAGE_SIZE + 1 : 0));
const pageEnd = computed(() => Math.min(total.value, currentPage.value * PAGE_SIZE));
const selectedItems = computed(() => items.value.filter((item) => selectedIds.value.has(item.id)));
const selectedCount = computed(() => selectedIds.value.size);
const visibleItemIds = computed(() => items.value.map((item) => item.id));
const allVisibleSelected = computed(() => Boolean(items.value.length) && visibleItemIds.value.every((id) => selectedIds.value.has(id)));
const someVisibleSelected = computed(() => visibleItemIds.value.some((id) => selectedIds.value.has(id)));
const visiblePages = computed(() => {
  const totalCount = totalPages.value;
  const current = currentPage.value;
  const start = Math.max(1, Math.min(current - 2, totalCount - 4));
  const end = Math.min(totalCount, start + 4);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
});

function formatFileSize(bytes?: number) {
  if (!bytes) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
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
function ownerLabel(ownerId: string) {
  return ownerMap.value.get(ownerId)?.name || ownerMap.value.get(ownerId)?.username || ownerId || "未知用户";
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
  const currentId = ++requestId;
  loading.value = true;
  try {
    const data = await fetchImageLibrary({
      limit: PAGE_SIZE,
      offset: (nextPage - 1) * PAGE_SIZE,
      q: query.value.trim(),
      productId: 0,
      templateId: selectedTemplateId.value || 0,
      favorite: favoriteOnly.value,
      allOwners: isAdmin.value && viewScope.value === "all",
      ownerId: isAdmin.value && viewScope.value === "owner" ? selectedOwnerId.value : "",
    });
    if (currentId !== requestId) return;
    const maxPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (data.total > 0 && nextPage > maxPage) {
      currentPage.value = maxPage;
      await load(maxPage);
      return;
    }
    items.value = data.items;
    total.value = data.total;
    currentPage.value = nextPage;
    const visibleIds = new Set(data.items.map((item) => item.id));
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => visibleIds.has(id)));
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取历史图库失败");
  } finally {
    if (currentId === requestId) loading.value = false;
  }
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
  if (loading.value || page < 1 || page > totalPages.value || page === currentPage.value) return;
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
    const response = await fetch(item.image_url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    saveBlob(blob, `image-${item.id}.png`);
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
  void load(1);
}

watch([query, selectedTemplateId, favoriteOnly], () => {
  clearSelection();
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => void load(1), 350);
});
watch([viewScope, selectedOwnerId], () => {
  if (!isAdmin.value) return;
  clearSelection();
  void load(1);
});
watch(isAdmin, (value) => {
  if (value) void loadUsers();
});

onMounted(async () => {
  query.value = typeof route.query.search === "string" ? route.query.search : "";
  try {
    templates.value = (await fetchPromptTemplates()).items;
  } catch {
    templates.value = [];
  }
  if (isAdmin.value) {
    await loadUsers();
  }
  await load(1);
  window.addEventListener("image-library-search", globalSearch);
});
onBeforeUnmount(() => {
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
            <h1 class="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">历史图库</h1>
            <p class="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              共保存 {{ total }} 张生成结果，当前显示 {{ pageStart }}-{{ pageEnd }} 张。收藏、下载和资产检查都在图片上完成。
            </p>
          </div>
          <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]" :disabled="loading" @click="load(currentPage)">
            <LoaderCircle v-if="loading" class="size-4 animate-spin" />
            <RefreshCw v-else class="size-4" />
            刷新
          </button>
        </div>
        <div class="mt-5 grid gap-2 xl:grid-cols-[minmax(240px,520px)_190px_auto_auto]">
          <div class="relative">
            <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索 Prompt、模型或优化后的提示词" data-testid="library-search-input" />
          </div>
          <select v-model="selectedTemplateId" class="studio-input h-12 px-3">
            <option :value="null">全部模板</option>
            <option v-for="template in templates" :key="template.id" :value="template.id">{{ template.name }}</option>
          </select>
          <select v-if="isAdmin" v-model="viewScope" class="studio-input h-12 px-3">
            <option value="mine">我的图片</option>
            <option value="all">全部用户</option>
            <option value="owner">指定用户</option>
          </select>
          <select v-if="isAdmin && viewScope === 'owner'" v-model="selectedOwnerId" class="studio-input h-12 px-3">
            <option value="">选择用户</option>
            <option v-for="user in users" :key="user.id" :value="user.id">{{ user.name || user.username || user.id }}</option>
          </select>
          <label class="studio-button inline-flex h-12 w-fit cursor-pointer items-center gap-2 rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 text-sm dark:border-white/10 dark:bg-white/[0.04]">
            <input v-model="favoriteOnly" type="checkbox" class="size-4 accent-[#4F7CFF]" />
            只看收藏
          </label>
        </div>
      </div>

      <div v-if="loading && !items.length" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
        <div v-for="index in 12" :key="index" class="studio-skeleton h-[330px] rounded-[20px]" />
      </div>

      <div v-else-if="!items.length" class="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
        <div>
          <div class="mx-auto flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
            <ImageIcon class="size-5" />
          </div>
          <h2 class="mt-4 text-lg font-semibold">暂无图片资产</h2>
          <p class="mt-1 text-sm text-slate-500">在图片工作台完成生成后，结果会自动出现在这里。</p>
        </div>
      </div>

      <template v-else>
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
              <button type="button" class="block aspect-[4/3] w-full overflow-hidden bg-slate-100 text-left dark:bg-white/[0.04]" @click="selectedItemId = item.id">
                <img :src="thumbnail(item)" :alt="item.prompt || '生成图片'" class="h-full w-full object-cover transition duration-300 group-hover:scale-[1.01]" loading="lazy" decoding="async" />
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
              <p class="mt-3 line-clamp-2 text-sm leading-6 text-slate-700 dark:text-stone-200">{{ item.prompt || item.revised_prompt || '未记录 Prompt' }}</p>
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

      <div v-if="items.length || totalPages > 1" class="studio-card flex flex-col gap-3 bg-white px-4 py-3 dark:bg-[#171a21] sm:flex-row sm:items-center sm:justify-between">
        <div class="text-sm text-slate-500 dark:text-stone-400">
          第 {{ currentPage }} / {{ totalPages }} 页，显示 {{ pageStart }}-{{ pageEnd }} / {{ total }} 张
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300" :disabled="loading || currentPage <= 1" aria-label="上一页" @click="goToPage(currentPage - 1)">
            <ChevronLeft class="size-4" />
          </button>
          <button v-for="page in visiblePages" :key="page" type="button" class="studio-button inline-flex h-10 min-w-10 items-center justify-center rounded-xl border px-3 text-sm font-semibold" :class="page === currentPage ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:text-stone-300'" :disabled="loading" @click="goToPage(page)">
            {{ page }}
          </button>
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-stone-300" :disabled="loading || currentPage >= totalPages" aria-label="下一页" @click="goToPage(currentPage + 1)">
            <ChevronRight class="size-4" />
          </button>
        </div>
      </div>
    </div>
  </section>

  <BaseModal :open="Boolean(selectedItem)" title="图片详情" width-class="max-w-[980px]" @close="selectedItemId = null">
    <div v-if="selectedItem" class="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(300px,.8fr)]">
      <div class="overflow-hidden rounded-2xl bg-slate-100 dark:bg-white/[0.04]">
        <img :src="selectedItem.image_url" :alt="selectedItem.prompt || '生成图片'" class="h-auto max-h-[72dvh] w-full object-contain" loading="eager" decoding="async" />
      </div>
      <div class="space-y-4">
        <div>
          <h3 class="text-sm font-semibold">Prompt</h3>
          <p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600 dark:text-stone-300">{{ selectedItem.prompt || selectedItem.revised_prompt || '未记录' }}</p>
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

  <BaseModal :open="Boolean(deleteTarget)" title="确认删除图片" description="删除后这张图片会从历史图库移出，后续不会在图库列表中展示。" width-class="max-w-[460px]" :show-close="false" @close="deleteTarget = null">
    <div v-if="deleteTarget" class="space-y-4 p-5">
      <div class="flex gap-3 rounded-xl bg-slate-100 p-3 dark:bg-white/[0.06]">
        <img :src="thumbnail(deleteTarget)" :alt="deleteTarget.prompt || '生成图片'" class="size-16 shrink-0 rounded-lg object-cover" loading="lazy" decoding="async" />
        <div class="min-w-0 text-sm">
          <p class="line-clamp-2 leading-6 text-slate-700 dark:text-stone-200">{{ deleteTarget.prompt || deleteTarget.revised_prompt || '未记录 Prompt' }}</p>
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
        <img v-for="item in selectedItems.slice(0, 12)" :key="item.id" :src="thumbnail(item)" :alt="item.prompt || '生成图片'" class="aspect-square rounded-lg object-cover" loading="lazy" decoding="async" />
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
