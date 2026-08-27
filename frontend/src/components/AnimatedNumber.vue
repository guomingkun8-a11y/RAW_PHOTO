<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{
  value: number | string | null | undefined;
  formatter?: (value: number) => string;
  duration?: number;
  fromZero?: boolean;
}>(), {
  duration: 480,
  fromZero: true,
});

const defaultFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const displayedValue = ref(0);
let animationFrame = 0;

const targetValue = computed(() => {
  const number = typeof props.value === "number" ? props.value : Number(props.value);
  return Number.isFinite(number) ? number : 0;
});

const displayedLabel = computed(() => formatValue(displayedValue.value));
const targetLabel = computed(() => formatValue(targetValue.value));

function formatValue(value: number) {
  return props.formatter ? props.formatter(value) : defaultFormat.format(value);
}

function easeOutQuint(value: number) {
  return 1 - Math.pow(1 - value, 5);
}

function shouldReduceMotion() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function cancelAnimation() {
  if (!animationFrame || typeof window === "undefined") return;
  window.cancelAnimationFrame(animationFrame);
  animationFrame = 0;
}

function animateTo(nextValue: number) {
  cancelAnimation();

  if (shouldReduceMotion()) {
    displayedValue.value = nextValue;
    return;
  }

  const startValue = props.fromZero ? 0 : displayedValue.value;
  const duration = Math.max(120, props.duration);

  if (startValue === nextValue || typeof window === "undefined") {
    displayedValue.value = nextValue;
    return;
  }

  displayedValue.value = startValue;
  const startedAt = window.performance.now();

  function tick(now: number) {
    const progress = Math.min(1, (now - startedAt) / duration);
    const eased = easeOutQuint(progress);
    displayedValue.value = startValue + (nextValue - startValue) * eased;

    if (progress < 1) {
      animationFrame = window.requestAnimationFrame(tick);
    } else {
      displayedValue.value = nextValue;
      animationFrame = 0;
    }
  }

  animationFrame = window.requestAnimationFrame(tick);
}

onMounted(() => animateTo(targetValue.value));
watch(targetValue, (nextValue) => animateTo(nextValue));
onBeforeUnmount(cancelAnimation);
</script>

<template>
  <span :aria-label="targetLabel">
    <span aria-hidden="true">{{ displayedLabel }}</span>
  </span>
</template>
