<script setup lang="ts">
import { computed, ref, useId } from "vue";
import { CircleHelp, LoaderCircle } from "@lucide/vue";
import type { DshAgentModel, DshContextSnapshot } from "./api";

const props = defineProps<{ model?: DshAgentModel; snapshot?: DshContextSnapshot | null; draft: string; images: number; fileChars?: number }>();
const open = ref(false);
const id = useId();
const windowTokens = computed(() => props.model?.context_window || 0);
// File language is unknown before submission; use the conservative text estimate.
const draftTokens = computed(() => Math.ceil(Array.from(props.draft).reduce((sum, char) => sum + (/[^\x00-\x7F]/.test(char) ? 1.5 : 0.25), 0) + (props.fileChars || 0) * 1.5));
const used = computed(() => props.snapshot ? Math.max(0, props.snapshot.used_tokens) + draftTokens.value : null);
const percent = computed(() => used.value === null || !windowTokens.value ? null : used.value / windowTokens.value * 100);
const nearingLimit = computed(() => used.value !== null && used.value >= (props.model?.compact_at_tokens || Infinity));
const switched = computed(() => !!props.snapshot && props.snapshot.model !== props.model?.id);
const label = computed(() => props.snapshot?.compacting ? "正在压缩上下文" : percent.value === null ? "上下文用量待测量" : `上下文估算已用 ${Math.round(percent.value)}%`);
const number = (value: number) => value.toLocaleString("zh-CN");

function onFocusOut(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) open.value = false;
}
</script>

<template>
  <div class="dsh-context" @mouseenter="open = true" @mouseleave="open = false" @focusout="onFocusOut" @keydown.esc="open = false">
    <button type="button" class="dsh-context-trigger" :class="{ 'is-warning': nearingLimit || snapshot?.compaction_failed }" :aria-label="label" :aria-expanded="open" :aria-controls="id" @click="open = true" @focus="open = true">
      <LoaderCircle v-if="snapshot?.compacting" class="size-4 animate-spin" />
      <CircleHelp v-else-if="percent === null" class="size-4" />
      <span v-else class="dsh-context-ring" :style="{ '--context-fill': `${Math.min(100, percent)}%` }" aria-hidden="true" />
    </button>
    <section v-if="open" :id="id" class="dsh-context-popover" role="region" aria-label="上下文详情">
      <header><strong>上下文</strong><span>{{ percent === null ? '待测量' : `约 ${Math.round(percent)}%` }}</span></header>
      <dl>
        <div><dt>当前用量估算</dt><dd>{{ used === null ? '待测量' : `${number(used)} tokens` }}</dd></div>
        <div><dt>系统配置上限</dt><dd>{{ windowTokens ? `${number(windowTokens)} tokens` : '未提供' }}</dd></div>
        <div v-if="model?.max_output_tokens"><dt>回答预留</dt><dd>{{ number(model.max_output_tokens) }} tokens</dd></div>
        <div v-if="model?.compact_at_tokens"><dt>自动压缩阈值</dt><dd>{{ number(model.compact_at_tokens) }} tokens</dd></div>
      </dl>
      <p v-if="snapshot?.compacting" role="status">正在整理较早的对话…</p>
      <p v-else-if="snapshot?.compaction_failed" role="status">压缩未完成，历史记录已保留。</p>
      <p v-else-if="switched">已按新模型额度重算，实际占用将在发送后更新。</p>
      <p v-else-if="snapshot?.compacted">较早的对话已压缩，完整聊天记录仍保留。</p>
      <p v-else-if="nearingLimit">下次请求前将尝试压缩较早的对话。</p>
      <p v-else-if="!snapshot">发送后更新实际会话估算。</p>
      <p v-if="images">新参考图的占用将在发送后计入。</p>
      <small>用量为估算，上限为系统配置，并非官方容量或累计消费。</small>
    </section>
  </div>
</template>

<style scoped>
.dsh-context { position: relative; flex: none; }
.dsh-context-trigger { display: grid; width: 32px; height: 36px; place-items: center; border: 0; border-radius: 6px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-context-trigger:hover, .dsh-context-trigger[aria-expanded="true"] { background: var(--studio-hover); color: var(--foreground); }
.dsh-context-trigger:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
.dsh-context-trigger.is-warning { color: var(--destructive); }
.dsh-context-ring { width: 17px; height: 17px; border-radius: 50%; background: conic-gradient(currentColor var(--context-fill), var(--input) 0); mask: radial-gradient(transparent 52%, #000 57%); }
.dsh-context-popover { position: absolute; z-index: 20; right: -40px; bottom: calc(100% + 8px); width: min(288px, calc(100vw - 40px)); border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); padding: 14px; color: var(--foreground); box-shadow: var(--studio-shadow); font-size: 12px; line-height: 1.6; }
.dsh-context-popover::after { content: ''; position: absolute; top: 100%; left: 0; right: 0; height: 10px; }
.dsh-context-popover header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.dsh-context-popover strong { font-size: 13px; font-weight: 600; }
.dsh-context-popover dl { display: grid; gap: 5px; margin: 0; }
.dsh-context-popover dl > div { display: flex; justify-content: space-between; gap: 12px; }
.dsh-context-popover dt { color: var(--muted-foreground); }
.dsh-context-popover dd { margin: 0; font-variant-numeric: tabular-nums; }
.dsh-context-popover p { margin: 10px 0 0; }
.dsh-context-popover small { display: block; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--studio-border); color: var(--muted-foreground); font-size: 11px; }
@media (pointer: coarse) { .dsh-context-trigger { width: 40px; height: 44px; } }
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } }
</style>
