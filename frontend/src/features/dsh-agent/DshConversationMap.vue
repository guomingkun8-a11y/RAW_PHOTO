<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { DshAgentMessage } from "./api";

const props = defineProps<{ messages: DshAgentMessage[]; viewport: HTMLElement | null }>();
const active = ref("");
const visible = ref(new Set<string>());
const preview = ref<{ title: string; text: string; top: number; right: number }>();
const entries = computed(() => {
  const result: { id: string; title: string; preview: string }[] = [];
  for (const message of props.messages) {
    if (message.role === "user") result.push({ id: message.message_id, title: message.content, preview: "" });
    else if (message.role === "assistant" && result.length) result[result.length - 1]!.preview = message.content || "正在回复";
  }
  return result;
});
let observer: IntersectionObserver | undefined;
let revision = 0;
watch(() => [props.viewport, props.messages.map(m => m.message_id).join(":")], async () => {
  const current = ++revision;
  observer?.disconnect();
  visible.value = new Set();
  preview.value = undefined;
  await nextTick();
  if (!props.viewport || current !== revision) return;
  const turns = new Map<string, string>();
  let turn = "";
  for (const message of props.messages) { if (message.role === "user") turn = message.message_id; turns.set(message.message_id, turn); }
  const onScreen = new Set<string>();
  observer = new IntersectionObserver(changes => {
    for (const change of changes) {
      const id = (change.target as HTMLElement).dataset.messageId!;
      if (change.isIntersecting) onScreen.add(id); else onScreen.delete(id);
    }
    visible.value = new Set([...onScreen].map(id => turns.get(id) || ""));
    const first = props.messages.find(message => onScreen.has(message.message_id));
    if (first) active.value = turns.get(first.message_id) || "";
  }, { root: props.viewport, threshold: [0, 0.2] });
  props.viewport.querySelectorAll("[data-message-id]").forEach(element => observer!.observe(element));
}, { immediate: true });
onBeforeUnmount(() => { revision++; observer?.disconnect(); });
function select(id: string) {
  const root = props.viewport;
  const target = [...(root?.querySelectorAll<HTMLElement>("[data-message-id]") || [])].find(el => el.dataset.messageId === id);
  if (root && target) root.scrollTo({ top: root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top - 24,
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}
function show(entry: typeof entries.value[number], event: Event) {
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
  preview.value = { title: entry.title, text: entry.preview, top: Math.max(12, Math.min(bounds.top - 36, innerHeight - 160)), right: innerWidth - bounds.left + 8 };
}
</script>

<template>
  <nav v-if="entries.length > 1" class="dsh-conversation-map" aria-label="对话导航" @mouseleave="preview = undefined" @focusout="preview = undefined">
    <button v-for="entry in entries" :key="entry.id" type="button" :aria-label="`定位对话：${entry.title.slice(0, 40)}`" :aria-current="active === entry.id ? 'location' : undefined" :class="{ 'is-visible': visible.has(entry.id) }" @mouseenter="show(entry, $event)" @focus="show(entry, $event)" @click="select(entry.id)"><span /></button>
    <Teleport to="body"><div v-if="preview" class="dsh-map-preview" role="tooltip" :style="{ top: `${preview.top}px`, right: `${preview.right}px` }"><strong>{{ preview.title }}</strong><p>{{ preview.text }}</p></div></Teleport>
  </nav>
</template>

<style scoped>
.dsh-conversation-map { position: absolute; z-index: 2; top: 50%; right: 12px; display: flex; flex-direction: column; width: 28px; max-height: 60%; overflow-y: auto; scrollbar-width: none; transform: translateY(-50%); }
.dsh-conversation-map button { display: grid; min-height: 16px; width: 28px; flex: none; place-items: center; border: 0; background: transparent; padding: 0; cursor: pointer; }
.dsh-conversation-map button span { width: 10px; height: 2px; border-radius: 1px; background: var(--input); transition: width 150ms ease-out, background 150ms ease-out; }
.dsh-conversation-map .is-visible span { background: var(--muted-foreground); }
.dsh-conversation-map button[aria-current] span { width: 17px; background: var(--foreground); }
.dsh-conversation-map button:hover span, .dsh-conversation-map button:focus-visible span { width: 22px; background: var(--foreground); }
.dsh-conversation-map button:focus-visible { outline: 1px solid var(--ring); outline-offset: -2px; }
.dsh-map-preview { position: fixed; z-index: 45; width: 250px; pointer-events: none; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); padding: 12px; font-size: 12px; }
.dsh-map-preview strong, .dsh-map-preview p { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.dsh-map-preview strong { -webkit-line-clamp: 2; font-weight: 500; }
.dsh-map-preview p { margin: 7px 0 0; -webkit-line-clamp: 3; line-height: 1.6; color: var(--muted-foreground); }
@container (max-width: 700px) { .dsh-conversation-map { display: none; } }
@media (prefers-reduced-motion: reduce) { .dsh-conversation-map button span { transition: none; } }
</style>
