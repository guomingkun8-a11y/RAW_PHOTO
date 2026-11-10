<script setup lang="ts">
defineProps<{ compact?: boolean }>();
</script>

<template>
  <div class="dsh-typing" :class="{ 'is-compact': compact }" role="status" aria-label="正在等待回复"><span /><span /><span /></div>
</template>

<style scoped>
.dsh-typing { display: flex; width: 52px; min-height: 28px; align-items: center; gap: 5px; padding: 6px 4px; }
.dsh-typing span { width: 5px; height: 5px; border-radius: 50%; background: var(--muted-foreground); animation: dsh-typing-wave 1.35s ease-in-out infinite; }
.dsh-typing span:nth-child(2) { animation-delay: 150ms; }
.dsh-typing span:nth-child(3) { animation-delay: 300ms; }
.dsh-typing.is-compact { width: auto; min-height: 0; gap: 3px; padding: 0; }
.dsh-typing.is-compact span { width: 3px; height: 3px; background: currentColor; }
@keyframes dsh-typing-wave { 0%, 60%, 100% { transform: translateY(0); opacity: .5; } 30% { transform: translateY(-3px); opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .dsh-typing span { animation: none; opacity: .8; } }
</style>
