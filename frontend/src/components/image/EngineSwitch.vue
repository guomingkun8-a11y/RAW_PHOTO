<script setup lang="ts">
import type { Component } from "vue";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

type EngineSwitchOption = {
  value: string;
  label: string;
  icon?: Component;
  disabled?: boolean;
  title?: string;
};

const props = defineProps<{
  value: string;
  options: EngineSwitchOption[];
  ariaLabel?: string;
}>();

const emit = defineEmits<{
  change: [value: string];
}>();

const wrapRef = ref<HTMLElement | null>(null);
const indicator = ref({ x: 0, width: 0 });

function updateIndicator() {
  const wrap = wrapRef.value;
  const active = wrap?.querySelector<HTMLButtonElement>("button.is-on");
  if (!wrap || !active) return;
  const wrapRect = wrap.getBoundingClientRect();
  const activeRect = active.getBoundingClientRect();
  indicator.value = {
    x: activeRect.left - wrapRect.left - 4,
    width: activeRect.width,
  };
}

function refreshIndicator() {
  void nextTick(() => requestAnimationFrame(updateIndicator));
}

function selectOption(option: EngineSwitchOption) {
  if (option.disabled || option.value === props.value) return;
  emit("change", option.value);
}

let resizeObserver: ResizeObserver | null = null;

watch(() => [props.value, props.options.length], refreshIndicator, { flush: "post" });

onMounted(() => {
  refreshIndicator();
  window.addEventListener("resize", updateIndicator);
  if (typeof ResizeObserver !== "undefined" && wrapRef.value) {
    resizeObserver = new ResizeObserver(updateIndicator);
    resizeObserver.observe(wrapRef.value);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", updateIndicator);
  resizeObserver?.disconnect();
});
</script>

<template>
  <div
    ref="wrapRef"
    class="pill-toggle"
    role="group"
    :aria-label="ariaLabel || '模式选择'"
  >
    <span
      class="indicator"
      aria-hidden="true"
      :style="{ transform: `translateX(${indicator.x}px)`, width: `${indicator.width}px` }"
    />
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :class="{ 'is-on': value === option.value }"
      :disabled="option.disabled"
      :title="option.title"
      :aria-pressed="value === option.value"
      :aria-label="option.label"
      @click="selectOption(option)"
    >
      <component :is="option.icon" v-if="option.icon" class="size-3.5" aria-hidden="true" />
      <span>{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.pill-toggle {
  position: relative;
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 0.15rem;
  padding: 0.25rem;
  overflow: hidden;
  border: 1px solid rgb(15 23 42 / 0.08);
  border-radius: 999px;
  background: rgb(248 250 252);
  white-space: nowrap;
}

.indicator {
  position: absolute;
  top: 0.25rem;
  bottom: 0.25rem;
  left: 0;
  z-index: 1;
  border-radius: 999px;
  background: rgb(49 91 232);
  box-shadow: 0 2px 6px rgb(49 91 232 / 0.22);
  pointer-events: none;
  transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}

.pill-toggle button {
  position: relative;
  z-index: 2;
  display: inline-flex;
  min-height: 2.75rem;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: rgb(100 116 139);
  padding: 0.35rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 700;
  cursor: pointer;
  transition: color 160ms ease, opacity 160ms ease;
}

.pill-toggle button:hover,
.pill-toggle button:focus-visible {
  color: rgb(49 91 232);
  outline: none;
}

.pill-toggle button:focus-visible {
  box-shadow: 0 0 0 2px rgb(79 124 255 / 0.25);
}

.pill-toggle button.is-on {
  color: #fff;
}

.pill-toggle button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.dark .pill-toggle {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.06);
}

.dark .pill-toggle button {
  color: rgb(168 162 158);
}

.dark .pill-toggle button:hover,
.dark .pill-toggle button:focus-visible {
  color: rgb(191 219 254);
}

.dark .pill-toggle button.is-on {
  color: #fff;
}

@media (prefers-reduced-motion: reduce) {
  .indicator,
  .pill-toggle button {
    transition: none;
  }
}
</style>
