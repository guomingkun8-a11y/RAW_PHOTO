<script setup lang="ts">
import { ArrowRight, LoaderCircle, Pencil, Plus, RefreshCw, Search, Sparkles, Trash2 } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { useRouter } from "vue-router";

import BaseModal from "@/components/BaseModal.vue";
import { createPromptTemplate, deletePromptTemplate, fetchPromptTemplates, updatePromptTemplate, type PromptTemplate } from "@/lib/api";
import { PROMPT_TEMPLATE_USE_STORAGE_KEY } from "@/lib/storage-namespace";
import { sessionState } from "@/stores/session";

type Draft = {
  id?: number;
  name: string;
  category: string;
  content: string;
};

const emptyDraft = (): Draft => ({
  name: "",
  category: "电商",
  content: "",
});

const items = ref<PromptTemplate[]>([]);
const query = ref("");
const category = ref("all");
const loading = ref(true);
const saving = ref(false);
const editing = ref<Draft | null>(null);
const router = useRouter();
const isAdmin = computed(() => sessionState.session?.role === "admin");

const categories = computed(() => Array.from(new Set(items.value.map((item) => item.category).filter(Boolean))));
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  return items.value.filter((item) => (
    category.value === "all" || item.category === category.value
  ) && (!keyword || [item.name, item.category, item.content].some((value) => String(value || "").toLowerCase().includes(keyword))));
});

async function load() {
  loading.value = true;
  try {
    items.value = (await fetchPromptTemplates()).items;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取模板失败");
  } finally {
    loading.value = false;
  }
}

function edit(item?: PromptTemplate) {
  editing.value = item
    ? {
        id: item.id,
        name: item.name,
        category: item.category,
        content: item.content,
      }
    : emptyDraft();
}

async function save() {
  if (!editing.value?.name.trim() || !editing.value.content.trim()) {
    toast.error("请输入模板名称和 Prompt 内容");
    return;
  }
  saving.value = true;
  try {
    const body = {
      ...editing.value,
      name: editing.value.name.trim(),
      category: editing.value.category.trim() || "通用",
      content: editing.value.content.trim(),
    };
    if (editing.value.id) await updatePromptTemplate(editing.value.id, body);
    else await createPromptTemplate(body);
    editing.value = null;
    await load();
    toast.success("模板已保存");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "保存模板失败");
  } finally {
    saving.value = false;
  }
}

async function remove(item: PromptTemplate) {
  if (!window.confirm(`确定删除模板“${item.name}”吗？删除后无法恢复。`)) return;
  try {
    await deletePromptTemplate(item.id);
    await load();
    toast.success("模板已删除");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除模板失败");
  }
}

async function useTemplate(item: PromptTemplate) {
  sessionStorage.setItem(PROMPT_TEMPLATE_USE_STORAGE_KEY, JSON.stringify({ name: item.name, content: item.content }));
  await router.push({ path: "/image", query: { prompt_template: String(item.id) } });
}

onMounted(load);
</script>

<template>
  <section class="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <div class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div class="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">Prompt Assets</div>
            <h1 class="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">模板中心</h1>
            <p class="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">保存和复用团队常用的提示词模板。</p>
          </div>
          <div class="flex gap-2">
            <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]" @click="load">
              <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />
              刷新
            </button>
            <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" @click="edit()">
              <Plus class="size-4" />
              新建模板
            </button>
          </div>
        </div>

        <div class="mt-5 grid gap-2 md:grid-cols-[minmax(260px,1fr)_220px]">
          <div class="relative">
            <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索模板名称、分类或提示词" />
          </div>
          <select v-model="category" class="studio-input h-12 px-3">
            <option value="all">全部分类</option>
            <option v-for="item in categories" :key="item" :value="item">{{ item }}</option>
          </select>
        </div>
      </div>

      <div v-if="loading" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="index in 6" :key="index" class="studio-skeleton h-[260px] rounded-[20px]" />
      </div>

      <div v-else-if="!filtered.length" class="studio-card grid min-h-[340px] place-items-center bg-white text-center dark:bg-[#171a21]">
        <div>
          <Sparkles class="mx-auto size-9 text-slate-400" />
          <h2 class="mt-3 text-lg font-semibold">暂无模板</h2>
          <p class="mt-1 text-sm text-slate-500">新建模板后可直接在图片工作台中调用。</p>
        </div>
      </div>

      <div v-else class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="item in filtered" :key="item.id" class="studio-card flex min-h-[260px] flex-col bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-start justify-between gap-3">
            <div>
              <span class="rounded-full bg-[#4F7CFF]/10 px-2.5 py-1 text-[11px] font-semibold text-[#315be8]">{{ item.category }}</span>
              <h2 class="mt-3 text-lg font-semibold text-slate-950 dark:text-stone-50">{{ item.name }}</h2>
              <p v-if="isAdmin" class="mt-1 text-xs text-slate-500 dark:text-stone-400">创建者：{{ item.owner_name || item.owner_id || "未知用户" }}</p>
            </div>
          </div>
          <p class="mt-4 line-clamp-2 text-sm leading-6 text-slate-600 dark:text-stone-300">{{ item.content }}</p>
          <div class="mt-auto flex flex-wrap gap-2 pt-5">
            <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl bg-slate-950 px-3 text-xs font-semibold text-white dark:bg-white dark:text-slate-950" :data-testid="`use-template-${item.id}`" @click="useTemplate(item)">
              <ArrowRight class="size-3.5" />
              使用模板
            </button>
            <template v-if="item.can_manage">
              <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold dark:border-white/10" @click="edit(item)">
                <Pencil class="size-3.5" />
                编辑
              </button>
              <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-rose-100 px-3 text-xs font-semibold text-rose-600 dark:border-rose-400/20" @click="remove(item)">
                <Trash2 class="size-3.5" />
                删除
              </button>
            </template>
          </div>
        </article>
      </div>
    </div>
  </section>

  <BaseModal :open="Boolean(editing)" :title="editing?.id ? '编辑模板' : '新建模板'" description="模板会同步到图片工作台和画布。" width-class="max-w-[760px]" @close="editing = null">
    <div v-if="editing" class="grid gap-4 p-5">
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="grid gap-1.5 text-sm font-medium">
          模板名称
          <input v-model="editing.name" class="studio-input h-11 px-3" />
        </label>
        <label class="grid gap-1.5 text-sm font-medium">
          分类
          <input v-model="editing.category" class="studio-input h-11 px-3" />
        </label>
      </div>
      <label class="grid gap-1.5 text-sm font-medium">
        Prompt 内容
        <textarea v-model="editing.content" class="studio-input min-h-48 resize-y p-3 leading-6" />
      </label>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" @click="editing = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" :disabled="saving" @click="save">
          <LoaderCircle v-if="saving" class="size-4 animate-spin" />
          保存
        </button>
      </div>
    </div>
  </BaseModal>
</template>
