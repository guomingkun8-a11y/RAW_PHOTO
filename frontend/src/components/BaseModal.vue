<script setup lang="ts">
import { X } from "@lucide/vue";

withDefaults(defineProps<{
  open: boolean;
  title?: string;
  description?: string;
  widthClass?: string;
  showClose?: boolean;
}>(), {
  title: "",
  description: "",
  widthClass: "max-w-[520px]",
  showClose: true,
});

const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-[100] grid place-items-center bg-slate-950/55 p-4 backdrop-blur-sm" @mousedown.self="emit('close')">
      <section class="max-h-[92dvh] w-full overflow-hidden rounded-2xl border border-black/[0.08] bg-white shadow-[0_30px_90px_rgba(15,23,42,0.32)] dark:border-white/10 dark:bg-[#171a21]" :class="widthClass" role="dialog" aria-modal="true">
        <header v-if="title || showClose" class="flex items-start justify-between gap-4 border-b border-black/[0.06] px-5 py-4 dark:border-white/10">
          <div>
            <h2 v-if="title" class="text-lg font-semibold text-slate-950 dark:text-stone-50">{{ title }}</h2>
            <p v-if="description" class="mt-1 text-sm leading-6 text-slate-500 dark:text-stone-400">{{ description }}</p>
          </div>
          <button v-if="showClose" type="button" class="studio-button inline-flex size-9 shrink-0 items-center justify-center rounded-xl text-slate-500 hover:bg-slate-100 dark:hover:bg-white/[0.08]" aria-label="关闭" @click="emit('close')"><X class="size-4" /></button>
        </header>
        <div class="max-h-[calc(92dvh-78px)] overflow-y-auto"><slot /></div>
      </section>
    </div>
  </Teleport>
</template>
