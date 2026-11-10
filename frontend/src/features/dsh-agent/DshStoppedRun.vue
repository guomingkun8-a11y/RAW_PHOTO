<script setup lang="ts">
import { ArrowRight, CircleStop, LoaderCircle, X } from "@lucide/vue";
defineProps<{ disabled: boolean; discarding: boolean; canContinue: boolean; hasImages: boolean }>();
const emit = defineEmits<{ continue: []; discard: [] }>();
</script>

<template>
  <div class="dsh-stopped-run">
    <div class="dsh-stopped-controls">
      <span role="status"><CircleStop />已停止</span>
      <button v-if="canContinue" type="button" :disabled="disabled" @click="emit('continue')"><ArrowRight />继续回答</button>
      <button type="button" :disabled="disabled || discarding" @click="emit('discard')"><LoaderCircle v-if="discarding" class="spinning" /><X v-else />丢弃</button>
    </div>
    <p v-if="hasImages">已提交的图片任务仍会继续。</p>
  </div>
</template>

<style scoped>
.dsh-stopped-run { margin-top: 12px; color: var(--muted-foreground); font-size: 12px; }
.dsh-stopped-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
.dsh-stopped-controls > span { display: inline-flex; align-items: center; gap: 5px; padding: 3px 7px; border: 1px solid var(--studio-border); border-radius: 5px; background: var(--studio-bg); }
.dsh-stopped-controls button { display: inline-flex; min-height: 32px; align-items: center; justify-content: center; gap: 5px; padding: 5px; border: 0; border-radius: 5px; background: transparent; color: color-mix(in srgb, var(--accent) 80%, var(--foreground)); cursor: pointer; }
.dsh-stopped-controls button:last-child { color: var(--muted-foreground); }
.dsh-stopped-controls button:hover:not(:disabled) { background: var(--muted); }
.dsh-stopped-controls button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.dsh-stopped-controls button:disabled { opacity: .4; cursor: not-allowed; }
.dsh-stopped-run svg { width: 14px; height: 14px; flex: none; }
.dsh-stopped-run p { margin: 6px 0 0; font-size: 11px; }
.spinning { animation: dsh-stopped-spin 1s linear infinite; }
@keyframes dsh-stopped-spin { to { transform: rotate(360deg); } }
@media (pointer: coarse) { .dsh-stopped-controls button { min-height: 44px; } }
@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
