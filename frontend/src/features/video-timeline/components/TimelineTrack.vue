<script setup lang="ts">
import type { Component } from 'vue';

defineProps<{
  label: string;
  description: string;
  icon: Component;
  muted?: boolean;
}>();
</script>

<template>
  <section class="timeline-track" :class="{ muted }" :aria-label="label">
    <header class="timeline-track-label">
      <component :is="icon" :size="16" aria-hidden="true" />
      <span>
        <strong>{{ label }}</strong>
        <small>{{ description }}</small>
      </span>
      <slot name="action" />
    </header>
    <div class="timeline-track-lane">
      <slot />
    </div>
  </section>
</template>
