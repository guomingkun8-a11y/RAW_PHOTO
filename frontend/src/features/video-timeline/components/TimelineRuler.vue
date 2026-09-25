<script setup lang="ts">
import { computed } from 'vue';

import { formatTimelineTime } from '@/features/video-timeline/composables/useTimeline';

const props = defineProps<{ duration: number; pixelsPerSecond: number }>();
const ticks = computed(() => {
  const interval = props.duration > 120 ? 20 : props.duration > 60 ? 10 : 5;
  const values: number[] = [];
  for (let value = 0; value <= Math.max(interval, props.duration); value += interval) values.push(value);
  return values;
});
</script>

<template>
  <div class="timeline-ruler" :style="{ width: `${Math.max(640, duration * pixelsPerSecond)}px` }" aria-hidden="true">
    <span v-for="tick in ticks" :key="tick" :style="{ left: `${tick * pixelsPerSecond}px` }">
      {{ formatTimelineTime(tick) }}
    </span>
  </div>
</template>
