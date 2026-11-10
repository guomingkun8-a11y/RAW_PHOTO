<script setup lang="ts">
import { Check, Copy, Download, Ellipsis, LoaderCircle, RefreshCw, ThumbsDown, ThumbsUp } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { rateDshAgentMessage, type DshAgentMessage } from "./api";

const props = defineProps<{ message: DshAgentMessage; canRetry: boolean; busy: boolean }>();
const emit = defineEmits<{ retry: []; feedback: [value: DshAgentMessage["feedback"]] }>();
const copied = ref(false);
const saving = ref(false);
const menuOpen = ref(false);
const root = ref<HTMLElement>();
const menuButton = ref<HTMLButtonElement>();
const canRate = computed(() => !!props.message.content && !props.message.message_id.startsWith("local-"));
let copyTimer: ReturnType<typeof setTimeout>;

async function copy() {
  try {
    await navigator.clipboard.writeText(props.message.content);
    copied.value = true;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { copied.value = false; }, 3000);
  } catch {
    toast.error("复制失败，请检查浏览器剪贴板权限");
  }
}

async function rate(value: "positive" | "negative") {
  if (saving.value) return;
  saving.value = true;
  try {
    const result = await rateDshAgentMessage(props.message, props.message.feedback === value ? null : value);
    emit("feedback", result.feedback);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "评价保存失败");
  } finally {
    saving.value = false;
  }
}

function exportMarkdown() {
  const url = URL.createObjectURL(new Blob([props.message.content], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `AI-${props.message.message_id}.md`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  closeMenu();
}

function closeMenu() {
  menuOpen.value = false;
  menuButton.value?.focus();
}
function clickOutside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) menuOpen.value = false;
}
function focusOutside(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node)) menuOpen.value = false;
}
onMounted(() => document.addEventListener("pointerdown", clickOutside));
onBeforeUnmount(() => {
  clearTimeout(copyTimer);
  document.removeEventListener("pointerdown", clickOutside);
});
</script>

<template>
  <div ref="root" class="dsh-message-actions" role="group" aria-label="回复操作" @keydown.esc.stop="closeMenu">
    <button type="button" :disabled="!message.content" :class="{ confirmed: copied }" :aria-label="copied ? '已复制' : '复制回复'" :title="copied ? '已复制' : '复制回复'" @click="copy"><Check v-if="copied" /><Copy v-else /></button>
    <button type="button" :disabled="saving || !canRate" :class="{ selected: message.feedback === 'positive' }" :aria-pressed="message.feedback === 'positive'" aria-label="有帮助" title="有帮助" @click="rate('positive')"><LoaderCircle v-if="saving" class="spinning" /><ThumbsUp v-else /></button>
    <button type="button" :disabled="saving || !canRate" :class="{ selected: message.feedback === 'negative' }" :aria-pressed="message.feedback === 'negative'" aria-label="没帮助" title="没帮助" @click="rate('negative')"><ThumbsDown /></button>
    <button v-if="canRetry" type="button" :disabled="busy" aria-label="重新回答" title="重新回答，不重复生图" @click="emit('retry')"><RefreshCw /></button>
    <div class="dsh-message-more" @focusout="focusOutside">
      <button ref="menuButton" type="button" aria-label="更多回复操作" title="更多操作" aria-haspopup="menu" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen" @keydown.down.prevent="menuOpen = true; $nextTick(() => root?.querySelector<HTMLButtonElement>('[role=menuitem]')?.focus())"><Ellipsis /></button>
      <div v-if="menuOpen" class="dsh-message-menu" role="menu" aria-label="更多回复操作">
        <button type="button" role="menuitem" :disabled="!message.content" @click="exportMarkdown"><Download />导出 Markdown</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dsh-message-actions { display: flex; align-items: center; gap: 2px; margin-top: 10px; color: var(--muted-foreground); }
.dsh-message-actions button { display: inline-flex; width: 32px; height: 32px; flex: none; align-items: center; justify-content: center; border: 0; border-radius: 6px; background: transparent; color: inherit; cursor: pointer; transition: color 120ms ease-out, background-color 120ms ease-out; }
.dsh-message-actions svg { width: 15px; height: 15px; flex: none; }
.dsh-message-actions button:hover:not(:disabled) { color: var(--foreground); background: var(--muted); }
.dsh-message-actions button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.dsh-message-actions button:disabled { opacity: .4; cursor: not-allowed; }
.dsh-message-actions .selected { background: var(--studio-hover); color: color-mix(in srgb, var(--accent) 80%, var(--foreground)); }
.dsh-message-actions .confirmed { color: color-mix(in srgb, var(--ai-success) 65%, var(--foreground)); }
.dsh-message-more { position: relative; }
.dsh-message-menu { position: absolute; z-index: 10; bottom: calc(100% + 5px); right: 0; width: 190px; max-width: calc(100vw - 90px); padding: 4px; border: 1px solid var(--input); border-radius: 8px; background: var(--studio-panel); box-shadow: var(--studio-shadow); }
.dsh-message-menu button { justify-content: flex-start; gap: 8px; width: 100%; height: auto; min-height: 36px; padding: 8px; font-size: 12px; text-align: left; }
.spinning { animation: dsh-action-spin 1s linear infinite; }
@keyframes dsh-action-spin { to { transform: rotate(360deg); } }
@media (pointer: coarse) { .dsh-message-actions button { width: 40px; height: 40px; } .dsh-message-menu button { width: 100%; } }
@media (prefers-reduced-motion: reduce) { .dsh-message-actions button { transition: none; } .spinning { animation: none; } }
</style>
