<script setup lang="ts">
defineProps<{ state: "queued" | "running" | "loading" }>();
</script>

<template>
  <div class="image-placeholder image-generation" :class="{ 'is-pending': state !== 'loading' }" role="status">
    <div class="generation-dots" aria-hidden="true">
      <span v-for="dot in 64" :key="dot" :style="{ animationDelay: `${((dot - 1) % 8 + Math.floor((dot - 1) / 8)) * 90}ms` }" />
    </div>
    <div class="generation-caption">
      <strong>{{ state === 'queued' ? '排队中' : state === 'loading' ? '正在加载图片' : '正在生成图片' }}</strong>
      <small>{{ state === 'loading' ? '图片已生成，正在加载' : '完成后将在这里显示' }}</small>
    </div>
  </div>
</template>

<style scoped>
.image-generation { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; padding: 16px; background: var(--secondary); color: var(--muted-foreground); text-align: center; }
.generation-dots { display: grid; grid-template-columns: repeat(8, 1fr); place-items: center; width: min(46%, 144px); aspect-ratio: 1; min-height: 0; flex: 0 1 144px; }
.generation-dots span { width: 4px; height: 4px; border-radius: 50%; background: currentColor; opacity: .2; animation: generation-dot 2.4s ease-in-out infinite; }
.generation-caption { display: grid; flex: none; gap: 5px; }
.generation-caption strong { color: var(--foreground); font-size: 13px; font-weight: 500; }
.generation-caption small { font-size: 11px; }
@keyframes generation-dot { 45% { opacity: .75; transform: scale(1.25); } }
@media (prefers-reduced-motion: reduce) { .generation-dots span { animation: none; opacity: .45; } }
</style>
