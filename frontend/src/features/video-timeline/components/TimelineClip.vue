<script setup lang="ts">
import { GripVertical, Video } from '@lucide/vue';

import type { TimelineVideoClip } from '@/features/video-timeline/types/timeline';
import { formatTimelineTime } from '@/features/video-timeline/composables/useTimeline';

defineProps<{
  clip: TimelineVideoClip;
  selected: boolean;
  width: number;
  index: number;
}>();

const emit = defineEmits<{
  select: [];
  dragstart: [event: DragEvent];
  dragend: [];
}>();
</script>

<template>
  <button
    class="timeline-video-clip"
    :class="{ selected, 'is-compact': width < 148, 'is-overlay': clip.track > 0, 'is-overlay-top': clip.track === 2 }"
    :style="{ width: `${width}px` }"
    type="button"
    draggable="true"
    :aria-label="`选择片段 ${clip.title}`"
    @click="emit('select')"
    @dragstart="emit('dragstart', $event)"
    @dragend="emit('dragend')"
  >
    <span class="timeline-clip-index">{{ String(index + 1).padStart(2, '0') }}</span>
    <span class="timeline-clip-preview">
      <video v-if="clip.thumbnailUrl || clip.sourceUrl" :src="clip.thumbnailUrl || clip.sourceUrl" muted preload="metadata" />
      <Video v-else :size="18" aria-hidden="true" />
    </span>
    <span class="timeline-clip-copy">
      <strong :title="clip.title">{{ clip.title }}</strong>
      <small>
        {{ formatTimelineTime(clip.duration) }}<template v-if="clip.track > 0"> · {{ Math.round(clip.opacity * 100) }}%</template>
      </small>
    </span>
    <GripVertical class="timeline-clip-grip" :size="15" aria-hidden="true" />
  </button>
</template>
