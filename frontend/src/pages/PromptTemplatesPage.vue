<script setup lang="ts">
import { Archive, LoaderCircle, Pencil, Plus, RefreshCw, Search, Sparkles } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import { createPromptTemplate, disablePromptTemplate, fetchPromptTemplates, updatePromptTemplate, type PromptTemplate } from "@/lib/api";
import { IMAGE_MODEL_CATALOG, formatImageModel } from "@/lib/image-models";

type Draft = {
  id?: number;
  name: string;
  category: string;
  content: string;
  model: string;
  size: string;
  quality: string;
  preserve_subject: boolean;
  enabled: boolean;
};

const defaultModel = IMAGE_MODEL_CATALOG[0]?.id || "gpt-image-2";
const emptyDraft = (): Draft => ({
  name: "",
  category: "电商",
  content: "",
  model: defaultModel,
  size: "1024x1024",
  quality: "auto",
  preserve_subject: false,
  enabled: true,
});

const items = ref<PromptTemplate[]>([]);
const query = ref("");
const category = ref("all");
const loading = ref(true);
const saving = ref(false);
const editing = ref<Draft | null>(null);

const categories = computed(() => Array.from(new Set(items.value.map((item) => item.category).filter(Boolean))));
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  return items.value.filter((item) => (
    category.value === "all" || item.category === category.value
  ) && (!keyword || [item.name, item.category, item.content, item.model].some((value) => String(value || "").toLowerCase().includes(keyword))));
});

async function load() {
  loading.value = true;
  try {
    items.value = (await fetchPromptTemplates({ includeDisabled: true })).items;
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
        model: item.model || defaultModel,
        size: item.size || "1024x1024",
        quality: item.quality || "auto",
        preserve_subject: item.preserve_subject,
        enabled: item.enabled,
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

async function disable(item: PromptTemplate) {
  try {
    await disablePromptTemplate(item.id);
    await load();
    toast.success("模板已停用");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "停用模板失败");
  }
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
            <p class="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">把常用 Prompt、模型、尺寸和主体保真配置沉淀为团队资产。</p>
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
            <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索模板名称、分类、Prompt 或模型" />
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
            </div>
            <span class="rounded-full px-2.5 py-1 text-[11px] font-semibold" :class="item.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300' : 'bg-slate-100 text-slate-500 dark:bg-white/[0.08]'">{{ item.enabled ? '启用' : '停用' }}</span>
          </div>
          <p class="mt-4 line-clamp-2 text-sm leading-6 text-slate-600 dark:text-stone-300">{{ item.content }}</p>
          <div class="mt-4 flex flex-wrap gap-2 text-[11px] text-slate-500">
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ item.model ? formatImageModel(item.model) : '默认模型' }}</span>
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ item.size || '自动尺寸' }}</span>
            <span class="rounded-full bg-slate-100 px-2 py-1 dark:bg-white/[0.08]">{{ item.quality || 'auto' }}</span>
            <span v-if="item.preserve_subject" class="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[#315be8]">主体保真</span>
          </div>
          <div class="mt-auto flex gap-2 pt-5">
            <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold dark:border-white/10" @click="edit(item)">
              <Pencil class="size-3.5" />
              编辑
            </button>
            <button v-if="item.enabled" type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-rose-100 px-3 text-xs font-semibold text-rose-600 dark:border-rose-400/20" @click="disable(item)">
              <Archive class="size-3.5" />
              停用
            </button>
          </div>
        </article>
      </div>
    </div>
  </section>

  <BaseModal :open="Boolean(editing)" :title="editing?.id ? '编辑模板' : '新建模板'" description="模板会同步到图片工作台的模型与画布设置。" width-class="max-w-[760px]" @close="editing = null">
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
        <label class="grid gap-1.5 text-sm font-medium">
          模型
          <select v-model="editing.model" class="studio-input h-11 px-3">
            <option v-for="model in IMAGE_MODEL_CATALOG" :key="model.id" :value="model.id">{{ model.label }} - {{ model.id }}</option>
          </select>
        </label>
        <label class="grid gap-1.5 text-sm font-medium">
          尺寸
          <input v-model="editing.size" class="studio-input h-11 px-3" placeholder="1024x1024" />
        </label>
        <label class="grid gap-1.5 text-sm font-medium">
          质量
          <select v-model="editing.quality" class="studio-input h-11 px-3">
            <option value="auto">自动</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
        </label>
        <div class="flex items-end gap-4 pb-2">
          <label class="inline-flex items-center gap-2 text-sm">
            <input v-model="editing.preserve_subject" type="checkbox" class="size-4 accent-[#4F7CFF]" />
            主体保真
          </label>
          <label class="inline-flex items-center gap-2 text-sm">
            <input v-model="editing.enabled" type="checkbox" class="size-4 accent-[#4F7CFF]" />
            启用模板
          </label>
        </div>
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
