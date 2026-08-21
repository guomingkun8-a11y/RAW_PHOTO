<script setup lang="ts">
import { computed } from "vue";

type InlineSegment = {
  text: string;
  kind: "text" | "strong" | "code" | "link";
  href?: string;
};

type ContentBlock =
  | { type: "heading"; level: number; content: InlineSegment[] }
  | { type: "paragraph" | "quote"; content: InlineSegment[] }
  | { type: "ordered-list" | "unordered-list"; items: InlineSegment[][] };

const props = withDefaults(defineProps<{
  content?: string;
  size?: "small" | "medium";
}>(), {
  content: "",
  size: "medium",
});

function parseInline(value: string): InlineSegment[] {
  const segments: InlineSegment[] = [];
  const pattern = /(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|<https?:\/\/[^>\s]+>|https?:\/\/[^\s<]+|\*\*[^*\n]+\*\*|`[^`\n]+`)/gi;
  let cursor = 0;

  for (const match of value.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) segments.push({ text: value.slice(cursor, index), kind: "text" });
    const token = match[0];
    const markdownLink = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/i);
    const autoLink = token.match(/^<(https?:\/\/[^>\s]+)>$/i);
    if (markdownLink) {
      segments.push({ text: markdownLink[1], href: markdownLink[2], kind: "link" });
    } else if (autoLink) {
      segments.push({ text: autoLink[1], href: autoLink[1], kind: "link" });
    } else if (/^https?:\/\//i.test(token)) {
      const href = token.replace(/[),.;!?，。；！？]+$/, "");
      const suffix = token.slice(href.length);
      segments.push({ text: href, href, kind: "link" });
      if (suffix) segments.push({ text: suffix, kind: "text" });
    } else if (token.startsWith("**")) {
      segments.push({ text: token.slice(2, -2), kind: "strong" });
    } else {
      segments.push({ text: token.slice(1, -1), kind: "code" });
    }
    cursor = index + token.length;
  }

  if (cursor < value.length) segments.push({ text: value.slice(cursor), kind: "text" });
  return segments.length ? segments : [{ text: value, kind: "text" }];
}

function normalizeContent(value: string) {
  return value
    .replace(/\r\n?/g, "\n")
    .replace(/([^\n])(\d+[.、]\s*\*\*[^*]+\*\*)/g, "$1\n$2")
    .trim();
}

function parseContent(value: string): ContentBlock[] {
  const blocks: ContentBlock[] = [];
  const lines = normalizeContent(value).split("\n");

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    const heading = line.match(/^(#{1,3})\s*(.+)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, content: parseInline(heading[2]) });
      continue;
    }

    const ordered = line.match(/^\d+[.)、]\s*(.+)$/);
    if (ordered) {
      const previous = blocks.at(-1);
      const item = parseInline(ordered[1]);
      if (previous?.type === "ordered-list") previous.items.push(item);
      else blocks.push({ type: "ordered-list", items: [item] });
      continue;
    }

    const unordered = line.match(/^[-*•]\s+(.+)$/);
    if (unordered) {
      const previous = blocks.at(-1);
      const item = parseInline(unordered[1]);
      if (previous?.type === "unordered-list") previous.items.push(item);
      else blocks.push({ type: "unordered-list", items: [item] });
      continue;
    }

    const quote = line.match(/^>\s*(.+)$/);
    if (quote) {
      blocks.push({ type: "quote", content: parseInline(quote[1]) });
      continue;
    }

    blocks.push({ type: "paragraph", content: parseInline(line) });
  }

  return blocks;
}

function blockContent(block: ContentBlock) {
  return "content" in block ? block.content : [];
}

function blockItems(block: ContentBlock) {
  return "items" in block ? block.items : [];
}

function headingTag(block: ContentBlock) {
  return `h${Math.min(4, ("level" in block ? block.level : 1) + 2)}`;
}

const blocks = computed(() => parseContent(props.content));
</script>

<template>
  <div class="agent-message-content" :class="`agent-message-content--${size}`">
    <template v-for="(block, blockIndex) in blocks" :key="blockIndex">
      <component :is="headingTag(block)" v-if="block.type === 'heading'" class="agent-message-heading">
        <template v-for="(segment, index) in blockContent(block)" :key="index">
          <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
          <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.kind === 'link'" :href="segment.href" target="_blank" rel="noopener noreferrer">{{ segment.text }}</a>
          <span v-else>{{ segment.text }}</span>
        </template>
      </component>

      <ol v-else-if="block.type === 'ordered-list'" class="agent-message-list agent-message-list--ordered">
        <li v-for="(item, itemIndex) in blockItems(block)" :key="itemIndex">
          <template v-for="(segment, index) in item" :key="index">
            <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
            <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
            <a v-else-if="segment.kind === 'link'" :href="segment.href" target="_blank" rel="noopener noreferrer">{{ segment.text }}</a>
            <span v-else>{{ segment.text }}</span>
          </template>
        </li>
      </ol>

      <ul v-else-if="block.type === 'unordered-list'" class="agent-message-list">
        <li v-for="(item, itemIndex) in blockItems(block)" :key="itemIndex">
          <template v-for="(segment, index) in item" :key="index">
            <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
            <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
            <a v-else-if="segment.kind === 'link'" :href="segment.href" target="_blank" rel="noopener noreferrer">{{ segment.text }}</a>
            <span v-else>{{ segment.text }}</span>
          </template>
        </li>
      </ul>

      <blockquote v-else-if="block.type === 'quote'">
        <template v-for="(segment, index) in blockContent(block)" :key="index">
          <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
          <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.kind === 'link'" :href="segment.href" target="_blank" rel="noopener noreferrer">{{ segment.text }}</a>
          <span v-else>{{ segment.text }}</span>
        </template>
      </blockquote>

      <p v-else>
        <template v-for="(segment, index) in blockContent(block)" :key="index">
          <strong v-if="segment.kind === 'strong'">{{ segment.text }}</strong>
          <code v-else-if="segment.kind === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.kind === 'link'" :href="segment.href" target="_blank" rel="noopener noreferrer">{{ segment.text }}</a>
          <span v-else>{{ segment.text }}</span>
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.agent-message-content {
  max-width: 72ch;
  color: rgb(71 85 105);
  line-height: 1.72;
  overflow-wrap: anywhere;
}

.agent-message-content--medium {
  font-size: 0.875rem;
}

.agent-message-content--small {
  font-size: 0.75rem;
}

.agent-message-content > * + * {
  margin-top: 0.65rem;
}

.agent-message-heading {
  color: rgb(15 23 42);
  font-size: 0.95em;
  font-weight: 750;
  line-height: 1.5;
}

.agent-message-list {
  display: grid;
  gap: 0.35rem;
  margin-left: 1.1rem;
  list-style: disc;
}

.agent-message-list--ordered {
  list-style: decimal;
}

.agent-message-list li {
  padding-left: 0.2rem;
}

.agent-message-content strong {
  color: rgb(30 41 59);
  font-weight: 750;
}

.agent-message-content code {
  border-radius: 0.3rem;
  background: rgb(15 23 42 / 0.06);
  padding: 0.1rem 0.3rem;
  color: rgb(30 64 175);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
}

.agent-message-content a {
  color: rgb(37 99 235);
  font-weight: 650;
  text-decoration: underline;
  text-decoration-color: rgb(37 99 235 / 0.35);
  text-underline-offset: 0.18em;
}

.agent-message-content a:hover {
  color: rgb(29 78 216);
  text-decoration-color: currentColor;
}

.agent-message-content blockquote {
  border: 1px solid rgb(15 23 42 / 0.08);
  border-radius: 0.5rem;
  background: rgb(15 23 42 / 0.035);
  padding: 0.6rem 0.7rem;
}

:global(.dark) .agent-message-content {
  color: rgb(214 211 209);
}

:global(.dark) .agent-message-heading,
:global(.dark) .agent-message-content strong {
  color: rgb(250 250 249);
}

:global(.dark) .agent-message-content code {
  background: rgb(255 255 255 / 0.08);
  color: rgb(191 219 254);
}

:global(.dark) .agent-message-content a {
  color: rgb(147 197 253);
  text-decoration-color: rgb(147 197 253 / 0.45);
}

:global(.dark) .agent-message-content a:hover {
  color: rgb(191 219 254);
}

:global(.dark) .agent-message-content blockquote {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.05);
}
</style>
