<script setup lang="ts">
import MarkdownIt from "markdown-it";
import { computed } from "vue";
import DshDiagram from "./DshDiagram.vue";
import { isDiagramFence } from "./diagram";

const props = defineProps<{ content: string; streaming?: boolean }>();
const markdown = new MarkdownIt({ html: false, breaks: true });
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index]!.attrSet("target", "_blank");
  tokens[index]!.attrSet("rel", "noopener noreferrer");
  return renderer.renderToken(tokens, index, options);
};
markdown.renderer.rules.table_open = () => '<div class="dsh-markdown-table"><table>';
markdown.renderer.rules.table_close = () => "</table></div>";
const blocks = computed(() => {
  const environment = {};
  const tokens = markdown.parse(props.content, environment);
  const blocks: ({ kind: "markdown"; html: string } | { kind: "diagram"; code: string })[] = [];
  let start = 0;
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index]!;
    if (token.type !== "fence" || token.level !== 0 || !isDiagramFence(token.info, token.content)) continue;
    if (start < index) blocks.push({ kind: "markdown", html: markdown.renderer.render(tokens.slice(start, index), markdown.options, environment) });
    blocks.push({ kind: "diagram", code: token.content });
    start = index + 1;
  }
  if (start < tokens.length) blocks.push({ kind: "markdown", html: markdown.renderer.render(tokens.slice(start), markdown.options, environment) });
  return blocks;
});
</script>

<template>
  <div class="dsh-markdown">
    <template v-for="(block, index) in blocks" :key="index">
      <DshDiagram v-if="block.kind === 'diagram'" :code="block.code" :streaming="streaming" />
      <div v-else class="dsh-markdown-fragment" v-html="block.html" />
    </template>
  </div>
</template>

<style scoped>
.dsh-markdown { min-width: 0; line-height: 1.75; overflow-wrap: anywhere; white-space: normal; }
.dsh-markdown-fragment :deep(> :first-child) { margin-top: 0; }
.dsh-markdown-fragment :deep(> :last-child) { margin-bottom: 0; }
.dsh-markdown :deep(p) { margin: .65em 0; }
.dsh-markdown :deep(:is(h1, h2, h3, h4, h5, h6)) { margin: 1.2em 0 .5em; font-weight: 700; line-height: 1.5; }
.dsh-markdown :deep(h1) { font-size: 1.28em; }
.dsh-markdown :deep(h2) { font-size: 1.18em; }
.dsh-markdown :deep(:is(h3, h4, h5, h6)) { font-size: 1.08em; }
.dsh-markdown :deep(strong) { font-weight: 700; }
.dsh-markdown :deep(em) { font-style: italic; }
.dsh-markdown :deep(:is(ul, ol)) { margin: .65em 0; padding-left: 1.5em; }
.dsh-markdown :deep(ul) { list-style: disc; }
.dsh-markdown :deep(ol) { list-style: decimal; }
.dsh-markdown :deep(li + li) { margin-top: .3em; }
.dsh-markdown :deep(li > :is(ul, ol, p)) { margin: .3em 0; }
.dsh-markdown :deep(a) { color: color-mix(in srgb, var(--accent) 80%, var(--foreground)); text-decoration: underline; text-underline-offset: .18em; }
.dsh-markdown :deep(a:focus-visible) { outline: 2px solid currentColor; outline-offset: 2px; }
.dsh-markdown :deep(code) { border-radius: 4px; background: var(--muted); padding: .1em .3em; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; }
.dsh-markdown :deep(pre) { max-width: 100%; overflow-x: auto; margin: .85em 0; border: 1px solid var(--studio-border); border-radius: 6px; background: var(--studio-bg); padding: 12px; white-space: pre; overflow-wrap: normal; }
.dsh-markdown :deep(pre code) { border-radius: 0; background: transparent; padding: 0; }
.dsh-markdown :deep(blockquote) { margin: .85em 0; border-left: 3px solid var(--input); padding-left: 1em; color: var(--muted-foreground); }
.dsh-markdown :deep(hr) { margin: 1.2em 0; border: 0; border-top: 1px solid var(--studio-border); }
.dsh-markdown :deep(.dsh-markdown-table) { max-width: 100%; overflow-x: auto; margin: .85em 0; }
.dsh-markdown :deep(table) { width: 100%; border-collapse: collapse; font-size: .95em; }
.dsh-markdown :deep(:is(th, td)) { min-width: 100px; border: 1px solid var(--studio-border); padding: .45em .7em; text-align: left; }
.dsh-markdown :deep(th) { background: var(--studio-bg); font-weight: 650; }
.dsh-markdown :deep(img) { max-width: 100%; height: auto; }
.dsh-markdown :deep(> :first-child) { margin-top: 0; }
.dsh-markdown :deep(> :last-child) { margin-bottom: 0; }
</style>
