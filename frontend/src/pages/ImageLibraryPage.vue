<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
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
  fetchImageLibrary,
  fetchPromptTemplates,
  updateImageLibraryItem,
  type ImageLibraryItem,
  type PromptTemplate,
} from "@/lib/api";

const PAGE_SIZE = 24;

const route = useRoute();
const items = ref<ImageLibraryItem[]>([]);
const templates = ref<PromptTemplate[]>([]);
const total = ref(0);
const currentPage = ref(1);
const loading = ref(true);
const query = ref("");
const selectedTemplateId = ref<number | null>(null);
const favoriteOnly = ref(false);
const selectedItemId = ref<number | null>(null);
let filterTimer = 0;
let requestId = 0;

const templateMap = computed(() => new Map(templates.value.map((item) => [item.id, item])));
const selectedItem = computed(() => items.value.find((item) => item.id === selectedItemId.value) || null);
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));
const pageStart = computed(() => (total.value ? (currentPage.value - 1) * PAGE_SIZE + 1 : 0));
const pageEnd = computed(() => Math.min(total.value, currentPage.value * PAGE_SIZE));
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
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取历史图库失败");
  } finally {
    if (currentId === requestId) loading.value = false;
  }
}

function goToPage(page: number) {
  if (loading.value || page < 1 || page > totalPages.value || page === currentPage.value) return;
  void load(page);
}

async function download(item: ImageLibraryItem) {
  try {
    const response = await fetch(item.image_url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `image-${item.id}.png`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "下载图片失败");
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
async function remove(item: ImageLibraryItem) {
  try {
    await updateImageLibraryItem(item.id, { deleted: true });
    if (selectedItemId.value === item.id) selectedItemId.value = null;
    toast.success("图片已移出图库");
    await load(currentPage.value);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除图片失败");
  }
}
function globalSearch(event: Event) {
  query.value = event instanceof CustomEvent ? String(event.detail?.query || "") : "";
  void load(1);
}

watch([query, selectedTemplateId, favoriteOnly], () => {
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => void load(1), 350);
});

onMounted(async () => {
  query.value = typeof route.query.search === "string" ? route.query.search : "";
  try {
    templates.value = (await fetchPromptTemplates()).items;
  } catch {
    templates.value = [];
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
        <div class="mt-5 grid gap-2 xl:grid-cols-[minmax(240px,520px)_190px_auto]">
          <div class="relative">
            <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索 Prompt、模型或优化后的提示词" />
          </div>
          <select v-model="selectedTemplateId" class="studio-input h-12 px-3">
            <option :value="null">全部模板</option>
            <option v-for="template in templates" :key="template.id" :value="template.id">{{ template.name }}</option>
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

      <div v-else class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
        <article v-for="item in items" :key="item.id" class="group studio-card flex min-h-[330px] flex-col overflow-hidden bg-white dark:bg-[#171a21]">
          <button type="button" class="block aspect-[4/3] w-full overflow-hidden bg-slate-100 text-left dark:bg-white/[0.04]" @click="selectedItemId = item.id">
            <img :src="thumbnail(item)" :alt="item.prompt || '生成图片'" class="h-full w-full object-cover transition duration-300 group-hover:scale-[1.01]" loading="lazy" />
          </button>
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
            <div class="mt-auto flex gap-1 border-t border-black/[0.06] pt-3 dark:border-white/10">
              <button type="button" class="studio-button inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl hover:bg-slate-100 dark:hover:bg-white/[0.08]" @click="download(item)">
                <Download class="size-3.5" />
                下载
              </button>
              <button type="button" class="studio-button inline-flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl text-rose-600 hover:bg-rose-50" @click="remove(item)">
                <Trash2 class="size-3.5" />
                删除
              </button>
            </div>
          </div>
        </article>
      </div>

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
        <img :src="selectedItem.image_url" :alt="selectedItem.prompt || '生成图片'" class="h-auto max-h-[72dvh] w-full object-contain" />
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
</template>
