<script setup lang="ts">
import {
  ClipboardPaste,
  Copy,
  Focus,
  Scan,
  ZoomIn,
  ZoomOut,
} from '@lucide/vue';

defineProps<{
  zoom: number;
  selectionCount: number;
  canLocate: boolean;
  canCopy: boolean;
  canPaste: boolean;
}>();

defineEmits<{
  zoomOut: [];
  resetViewport: [];
  zoomIn: [];
  fitAll: [];
  locateSelected: [];
  copy: [];
  paste: [];
}>();
</script>

<template>
  <div class="canvas-controls" aria-label="画布控制" @pointerdown.stop @contextmenu.stop.prevent>
    <div class="canvas-control-group">
      <button class="icon-button" type="button" title="缩小" aria-label="缩小" @click="$emit('zoomOut')"><ZoomOut :size="17" /></button>
      <button class="zoom-readout" type="button" title="重置画布" aria-label="重置画布" @click="$emit('resetViewport')">{{ Math.round(zoom * 100) }}%</button>
      <button class="icon-button" type="button" title="放大" aria-label="放大" @click="$emit('zoomIn')"><ZoomIn :size="17" /></button>
    </div>
    <span class="canvas-control-divider" aria-hidden="true" />
    <div class="canvas-control-group">
      <button class="icon-button" type="button" title="适配全部节点" aria-label="一键适配全部节点" @click="$emit('fitAll')"><Scan :size="17" /></button>
      <button class="icon-button" type="button" title="定位选中节点" aria-label="定位选中节点" :disabled="!canLocate" @click="$emit('locateSelected')"><Focus :size="17" /></button>
    </div>
    <span class="canvas-control-divider" aria-hidden="true" />
    <div class="canvas-control-group">
      <button class="icon-button" type="button" title="复制选中节点" aria-label="复制选中节点" :disabled="!canCopy" @click="$emit('copy')"><Copy :size="16" /></button>
      <button class="icon-button" type="button" title="粘贴节点" aria-label="粘贴节点" :disabled="!canPaste" @click="$emit('paste')"><ClipboardPaste :size="16" /></button>
    </div>
    <span v-if="selectionCount > 1" class="canvas-selection-count" :title="`已选择 ${selectionCount} 个节点`">{{ selectionCount }}</span>
  </div>
</template>
