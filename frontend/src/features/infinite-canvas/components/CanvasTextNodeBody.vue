<script setup lang="ts">
import { FileText, Link2, Trash2 } from '@lucide/vue';
import type { CanvasNode } from '@/features/infinite-canvas/types';

defineProps<{
  node: CanvasNode;
  connectionSource: boolean;
  statusLabel: string;
}>();

const emit = defineEmits<{
  select: [];
  updateContent: [value: string];
  remove: [];
}>();

function updateContent(event: Event) {
  emit('updateContent', (event.target as HTMLTextAreaElement).value);
}
</script>

<template>
  <header class="text-node-header">
    <div class="node-title-row">
      <FileText :size="14" aria-hidden="true" />
      <strong :title="node.title">{{ node.title }}</strong>
      <Link2 v-if="connectionSource" :size="13" aria-label="正在连接" />
      <button class="node-icon-button" type="button" title="删除节点" aria-label="删除节点" @pointerdown.stop @click.stop="emit('remove')">
        <Trash2 :size="13" aria-hidden="true" />
      </button>
    </div>
  </header>
  <textarea
    class="text-node-editor"
    :value="node.content"
    :aria-label="`${node.title}内容`"
    placeholder="输入文案、镜头描述或创意方向..."
    maxlength="4000"
    @focus="emit('select')"
    @pointerdown.stop
    @click.stop
    @dblclick.stop
    @input="updateContent"
  />
  <footer class="text-node-footer">
    <span class="node-status-badge" :class="`status-${node.status}`">{{ statusLabel }}</span>
    <span>{{ node.content.length }}/4000</span>
  </footer>
</template>
