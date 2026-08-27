<script setup lang="ts">
import { X } from "@lucide/vue";

defineProps<{
  open: boolean;
  imageUrl: string;
  title?: string;
  subtitle?: string;
}>();

const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <Teleport to="body">
    <div v-if="open && imageUrl" class="fixed inset-0 z-[130] grid place-items-center bg-slate-950/75 p-4" @mousedown.self="emit('close')">
      <section class="flex max-h-[92dvh] w-full max-w-[1120px] flex-col overflow-hidden rounded-2xl bg-white shadow-[0_24px_72px_rgba(15,23,42,0.35)] dark:bg-[#171a21]" role="dialog" aria-modal="true" :aria-label="title || '图片预览'">
        <header class="flex items-start justify-between gap-4 border-b border-black/[0.06] px-4 py-3 dark:border-white/10 sm:px-5">
          <div class="min-w-0">
            <h2 class="truncate text-base font-semibold text-slate-950 dark:text-stone-50">{{ title || "图片预览" }}</h2>
            <p v-if="subtitle" class="mt-1 truncate text-xs text-slate-500 dark:text-stone-400">{{ subtitle }}</p>
          </div>
          <button type="button" class="studio-button inline-flex size-9 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.08]" aria-label="关闭预览" @click="emit('close')">
            <X class="size-4" />
          </button>
        </header>
        <div class="grid min-h-0 flex-1 place-items-center bg-slate-950 p-3">
          <img :src="imageUrl" :alt="title || '图片预览'" class="max-h-[calc(92dvh-88px)] max-w-full object-contain" loading="eager" decoding="async" />
        </div>
      </section>
    </div>
  </Teleport>
</template>
