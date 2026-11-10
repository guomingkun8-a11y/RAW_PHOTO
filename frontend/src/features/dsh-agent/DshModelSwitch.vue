<script setup lang="ts">
import { ArrowRightLeft } from "@lucide/vue";
import { dshAgentModelLabel } from "@/features/dsh-agent/api";

defineProps<{ fromModel: string; toModel: string }>();
</script>

<template>
  <div class="dsh-model-switch" role="note" aria-label="模型切换记录">
    <span class="dsh-model-switch-line" />
    <ArrowRightLeft class="dsh-model-switch-icon" aria-hidden="true" />
    <span>已从 <strong>{{ dshAgentModelLabel(fromModel) }}</strong> 切换至 <strong>{{ dshAgentModelLabel(toModel) }}</strong></span>
    <span class="dsh-model-switch-line" />
  </div>
</template>

<style scoped>
.dsh-model-switch { display: flex; max-width: 760px; align-items: center; justify-content: center; gap: 9px; margin: 8px auto 26px; color: var(--muted-foreground); font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.dsh-model-switch-line { height: 1px; min-width: 12px; flex: 1; background: var(--studio-border); }
.dsh-model-switch-icon { width: 14px; height: 14px; flex: none; }
.dsh-model-switch strong { color: var(--muted-foreground); font-weight: 500; }
</style>
