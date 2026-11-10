<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { CornerDownLeft, ImagePlus, ListChecks, MessageSquare, Paintbrush, Search, Users } from "@lucide/vue";
import type { DshExpert } from "./api";

const props = defineProps<{
  menuId: string;
  input?: HTMLTextAreaElement;
  modelValue: string;
  disabled: boolean;
  experts?: DshExpert[];
  imageEnabled: boolean;
  teamsEnabled?: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  expert: [id: string];
  collaboration: [enabled: boolean];
}>();
const menu = ref<HTMLElement>();
const token = ref<{ start: number; end: number; query: string } | null>(null);
const active = ref(0);
const position = ref<Record<string, string>>({});
let composing = false;
let dismissed = "";
const commands = computed(() => [
  ...(props.experts || []).map(expert => ({ id: expert.id === "ecommerce-design" ? "design" : expert.id, label: expert.label, description: expert.description, icon: Paintbrush, expertId: expert.id, prompt: "" })),
  { id: "chat", label: "普通对话", description: "退出专家与协作模式", icon: MessageSquare, expertId: "", prompt: "" },
  ...(props.imageEnabled ? [{ id: "image", label: "生成图片", description: "描述要生成或修改的图片", icon: ImagePlus, prompt: "请生成图片：", expertId: "" }] : []),
  ...(props.teamsEnabled ? [{ id: "teams", label: "多智能体协作", description: "让多个智能体分工完成任务", icon: Users, prompt: "", expertId: "" }] : []),
  { id: "summary", label: "总结对话", description: "整理当前对话的要点与待办", icon: ListChecks, prompt: "请总结当前对话的要点和待办事项。", expertId: "" },
]);
const filtered = computed(() => {
  const query = token.value?.query.toLocaleLowerCase() || "";
  return commands.value.filter(command => `${command.id} ${command.label} ${command.description}`.toLocaleLowerCase().includes(query));
});
const isOpen = computed(() => !!token.value);
const activeId = computed(() => isOpen.value && filtered.value[active.value] ? `${props.menuId}-${filtered.value[active.value]!.id}` : undefined);
const signature = () => `${props.modelValue}:${props.input?.selectionStart}`;

function close() {
  dismissed = signature();
  token.value = null;
}

function refresh(event?: Event) {
  if (event?.type === "input") dismissed = "";
  const input = props.input;
  if (!input || document.activeElement !== input || props.disabled || composing || input.selectionStart !== input.selectionEnd || dismissed === signature()) { token.value = null; return; }
  const end = input.selectionStart;
  const match = props.modelValue.slice(0, end).match(/(?:^|\s)\/([^\s/]*)$/u);
  if (!match || /\S/u.test(props.modelValue[end] || "")) { token.value = null; return; }
  const query = match[1] || "";
  if (query !== token.value?.query) active.value = 0;
  token.value = { start: end - query.length - 1, end, query };
}

function placeMenu() {
  const input = props.input;
  if (!input || !isOpen.value) return;
  const bounds = (input.closest(".dsh-composer") || input).getBoundingClientRect();
  const width = Math.min(400, bounds.width, innerWidth - 24);
  const top = Math.max(12, Math.min(bounds.top, innerHeight - 12));
  const bottom = Math.max(12, Math.min(bounds.bottom, innerHeight - 12));
  const above = top - 20;
  const below = innerHeight - bottom - 20;
  position.value = {
    width: `${width}px`, left: `${Math.max(12, Math.min(bounds.left, innerWidth - width - 12))}px`,
    maxHeight: `${Math.max(80, Math.min(320, Math.max(above, below)))}px`,
    ...(above >= below ? { bottom: `${innerHeight - top + 8}px` } : { top: `${bottom + 8}px` }),
  };
}

async function choose(index: number) {
  const command = filtered.value[index];
  const range = token.value;
  if (!command || !range || props.disabled) return;
  const suffix = props.modelValue.slice(range.end);
  const value = props.modelValue.slice(0, range.start) + command.prompt + suffix;
  close();
  emit("update:modelValue", value);
  if (command.id === "chat" || command.expertId) emit("expert", command.expertId);
  if (command.id === "chat" || command.id === "teams") emit("collaboration", command.id === "teams");
  await nextTick();
  props.input?.focus({ preventScroll: true });
  const caret = range.start + command.prompt.length;
  props.input?.setSelectionRange(caret, caret);
}

function onKeydown(event: KeyboardEvent) {
  if (!isOpen.value || event.isComposing || event.keyCode === 229 || composing) return false;
  if (event.key === "Escape") { event.preventDefault(); close(); return true; }
  if (event.key === "Tab" && event.shiftKey) { close(); return false; }
  if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    if (filtered.value.length) active.value = (active.value + (event.key === "ArrowDown" ? 1 : -1) + filtered.value.length) % filtered.value.length;
    void nextTick(() => document.getElementById(activeId.value || "")?.scrollIntoView({ block: "nearest" }));
    return true;
  }
  if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
    if (event.key === "Tab" && !filtered.value.length) { close(); return false; }
    event.preventDefault();
    void choose(active.value);
    return true;
  }
  return false;
}

function onPointerDown(event: PointerEvent) {
  if (event.target !== props.input && !menu.value?.contains(event.target as Node)) close();
}
function compositionStart() { composing = true; token.value = null; }
function compositionEnd() { composing = false; dismissed = ""; refresh(); }

watch(() => props.input, (input, _previous, onCleanup) => {
  if (!input) return;
  const listeners: [string, EventListener][] = [["input", refresh], ["click", refresh], ["keyup", refresh], ["select", refresh], ["focus", refresh], ["blur", close], ["compositionstart", compositionStart], ["compositionend", compositionEnd]];
  for (const [name, handler] of listeners) input.addEventListener(name, handler);
  const observer = new ResizeObserver(placeMenu);
  observer.observe(input.closest(".dsh-composer") || input);
  onCleanup(() => { observer.disconnect(); for (const [name, handler] of listeners) input.removeEventListener(name, handler); });
}, { flush: "post" });
watch(() => [props.modelValue, props.disabled], () => refresh(), { flush: "post" });
watch(filtered, () => { active.value = Math.min(active.value, Math.max(0, filtered.value.length - 1)); });
watch(isOpen, async open => {
  await nextTick();
  if (open && isOpen.value) { placeMenu(); menu.value?.showPopover(); }
  else menu.value?.hidePopover();
});
onMounted(() => {
  document.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("resize", placeMenu);
  window.addEventListener("scroll", placeMenu, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onPointerDown);
  window.removeEventListener("resize", placeMenu);
  window.removeEventListener("scroll", placeMenu, true);
});
defineExpose({ onKeydown, isOpen, activeId });
</script>

<template>
  <div :id="menuId" ref="menu" popover="manual" class="dsh-slash-menu" :style="position" role="listbox" aria-label="斜杠命令">
    <button v-for="(command, index) in filtered" :id="`${menuId}-${command.id}`" :key="command.id" type="button" role="option" :aria-selected="index === active" tabindex="-1" @pointerdown.prevent @pointermove="active = index" @click="choose(index)">
      <component :is="command.icon" class="size-4 slash-icon" aria-hidden="true" />
      <span class="slash-copy"><strong><span>/{{ command.id }}</span>{{ command.label }}</strong><small>{{ command.description }}</small></span>
      <CornerDownLeft v-if="index === active" class="size-3.5 slash-enter" aria-hidden="true" />
    </button>
    <div v-if="!filtered.length" class="slash-empty"><Search class="size-4" aria-hidden="true" />没有匹配的命令</div>
  </div>
</template>

<style scoped>
.dsh-slash-menu { position: fixed; inset: auto; margin: 0; padding: 5px; overflow: auto; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); }
.dsh-slash-menu:popover-open { animation: slash-reveal 140ms ease-out; }
.dsh-slash-menu button { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 56px; padding: 9px 10px; border: 0; border-radius: 5px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.dsh-slash-menu button[aria-selected='true'] { background: var(--studio-hover); }
.slash-icon { flex: none; color: var(--muted-foreground); }
.slash-copy { display: grid; min-width: 0; flex: 1; gap: 4px; }
.slash-copy strong { display: flex; flex-wrap: wrap; gap: 4px 9px; font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }
.slash-copy strong > span { font-weight: 600; }
.slash-copy small { color: var(--muted-foreground); font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
.slash-enter { flex: none; color: var(--muted-foreground); }
.slash-empty { display: flex; align-items: center; gap: 8px; min-height: 56px; padding: 12px; color: var(--muted-foreground); font-size: 13px; }
@keyframes slash-reveal { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .dsh-slash-menu:popover-open { animation: none; } }
</style>
