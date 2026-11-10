<script setup lang="ts">
import { ChevronDown, ExternalLink } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, useId } from "vue";
import type { DshAgentActivity } from "./api";

const props = defineProps<{ activities: DshAgentActivity[] }>();
const id = useId();
const trigger = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();
const open = ref(false);
const position = ref<Record<string, string>>({});
const sources = computed(() => {
  const urls = new Map<string, { title: string; url: string; host: string }>();
  for (const activity of props.activities) for (const source of activity.sources || []) {
    try {
      const url = new URL(source.url);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) continue;
      urls.set(url.href, { title: source.title || url.hostname, url: url.href, host: url.hostname.replace(/^www\./, "") });
    } catch { /* Invalid links are not citations. */ }
  }
  return [...urls.values()];
});
function place() {
  const bounds = trigger.value?.getBoundingClientRect();
  if (!bounds) return;
  const width = Math.min(340, innerWidth - 24);
  const height = Math.min(340, sources.value.length * 72 + 46, innerHeight - 24);
  position.value = { width: `${width}px`, maxHeight: `${height}px`, left: `${Math.max(12, Math.min(bounds.left, innerWidth - width - 12))}px`,
    top: `${Math.max(12, bounds.bottom + height + 8 < innerHeight ? bounds.bottom + 8 : bounds.top - height - 8)}px` };
}
function toggle(event: Event) {
  open.value = (event as ToggleEvent).newState === "open";
  if (open.value) { place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true); }
  else cleanup();
}
function cleanup() { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); }
onBeforeUnmount(cleanup);
</script>

<template>
  <div v-if="sources.length" class="dsh-sources">
    <button ref="trigger" type="button" class="dsh-sources-trigger" :popovertarget="id" :aria-expanded="open" :aria-label="`来源 ${sources.length}`" @click="place">
      <span class="source-icons" aria-hidden="true"><span v-for="source in sources.slice(0, 3)" :key="source.url">{{ source.host[0]?.toUpperCase() }}</span></span>
      <span>来源 {{ sources.length }}</span><ChevronDown class="size-3" />
    </button>
    <div :id="id" ref="panel" popover="auto" class="dsh-sources-panel" :style="position" role="region" aria-label="搜索来源" @toggle="toggle">
      <header>搜索来源 <span>{{ sources.length }}</span></header>
      <a v-for="source in sources" :key="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">
        <span class="source-icon" aria-hidden="true">{{ source.host[0]?.toUpperCase() }}</span>
        <span><strong>{{ source.title }}</strong><small>{{ source.host }}</small></span><ExternalLink class="size-3.5" />
      </a>
    </div>
  </div>
</template>

<style scoped>
.dsh-sources { margin: 12px 0 4px; }
.dsh-sources-trigger { display: inline-flex; align-items: center; gap: 7px; min-height: 32px; border: 0; border-radius: 99px; background: var(--secondary); color: var(--muted-foreground); padding: 5px 10px; font-size: 12px; cursor: pointer; }
.dsh-sources-trigger:hover, .dsh-sources-trigger[aria-expanded='true'] { background: var(--studio-hover); color: var(--foreground); }
.source-icons { display: flex; padding-left: 3px; }
.source-icons > span { display: grid; width: 19px; height: 19px; margin-left: -3px; place-items: center; border: 2px solid var(--secondary); border-radius: 50%; background: var(--studio-panel); color: var(--muted-foreground); font-size: 9px; }
.dsh-sources-panel { position: fixed; inset: auto; margin: 0; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); padding: 6px; color: var(--foreground); font-size: 13px; scrollbar-width: thin; }
.dsh-sources-panel header { display: flex; gap: 8px; padding: 7px 8px 10px; font-weight: 600; }
.dsh-sources-panel header span { color: var(--muted-foreground); font-weight: 400; }
.dsh-sources-panel a { display: flex; align-items: center; gap: 10px; padding: 10px 8px; border-radius: 5px; text-decoration: none; color: inherit; }
.dsh-sources-panel a:hover { background: var(--studio-hover); }
.dsh-sources-panel a > span:nth-child(2) { min-width: 0; flex: 1; }
.dsh-sources-panel strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.dsh-sources-panel small { display: block; margin-top: 3px; color: var(--muted-foreground); font-size: 11px; }
.source-icon { display: grid; width: 28px; height: 28px; flex: none; place-items: center; border-radius: 6px; background: var(--secondary); }
button:focus-visible, a:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
</style>
