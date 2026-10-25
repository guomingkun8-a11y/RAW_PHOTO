<script setup lang="ts">
import { LoaderCircle, Music2, Trash2, Upload } from '@lucide/vue';
import type { CanvasNode } from '@/features/infinite-canvas/types';

defineProps<{
  node: CanvasNode;
  uploadEnabled: boolean;
  stageHeight: number;
}>();

const emit = defineEmits<{
  select: [];
  upload: [];
  remove: [];
}>();
</script>

<template>
  <div class="audio-node-body" @pointerdown.stop="emit('select')">
    <header class="media-node-header audio-node-header">
      <div class="media-node-title">
        <span class="media-node-icon"><Music2 :size="16" aria-hidden="true" /></span>
        <strong :title="node.title">{{ node.title }}</strong>
      </div>
      <div class="media-node-actions">
        <span class="media-status" :class="`status-${node.status}`">
          <LoaderCircle v-if="node.status === 'uploading'" class="spin" :size="12" aria-hidden="true" />
          {{ node.status === 'uploading' ? '上传中' : node.inputAudioUrl ? '已就绪' : '待上传' }}
        </span>
        <button class="node-icon-button" type="button" title="删除节点" aria-label="删除节点" @pointerdown.stop @click.stop="emit('remove')">
          <Trash2 :size="14" aria-hidden="true" />
        </button>
      </div>
    </header>

    <div class="media-node-stage audio-node-stage" :style="{ height: `${stageHeight}px`, minHeight: `${stageHeight}px` }">
      <audio
        v-if="node.inputAudioUrl"
        class="audio-node-player"
        :src="node.inputAudioUrl"
        controls
        preload="metadata"
        @pointerdown.stop
      />
      <button
        v-else
        class="media-upload-button"
        type="button"
        :disabled="!uploadEnabled || node.status === 'uploading'"
        @pointerdown.stop
        @click.stop="emit('upload')"
      >
        <span class="media-upload-icon"><Upload :size="22" aria-hidden="true" /></span>
        <strong>上传参考音频</strong>
        <small>{{ uploadEnabled ? '支持 MP3、WAV、M4A、AAC、OGG、FLAC' : '请先配置服务端存储' }}</small>
      </button>
    </div>
    <p v-if="node.error" class="media-node-error" :title="node.error">{{ node.error }}</p>
  </div>
</template>
