<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{
  value: number;
  duration?: number;
  label?: string;
}>(), {
  duration: 560,
  label: "成功率",
});

const radius = 76;
const circumference = 2 * Math.PI * radius;
const animatedRate = ref(0);
let animationFrame = 0;

const targetRate = computed(() => {
  const number = Number(props.value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(100, Math.max(0, number));
});
const strokeOffset = computed(() => circumference * (1 - animatedRate.value / 100));
const animatedLabel = computed(() => `${Math.round(animatedRate.value)}%`);
const targetLabel = computed(() => `${Math.round(targetRate.value)}%`);

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
    animatedRate.value = nextValue;
    return;
  }

  if (typeof window === "undefined") {
    animatedRate.value = nextValue;
    return;
  }

  animatedRate.value = 0;
  const startedAt = window.performance.now();
  const duration = Math.max(160, props.duration);

  function tick(now: number) {
    const progress = Math.min(1, (now - startedAt) / duration);
    animatedRate.value = nextValue * easeOutQuint(progress);

    if (progress < 1) {
      animationFrame = window.requestAnimationFrame(tick);
    } else {
      animatedRate.value = nextValue;
      animationFrame = 0;
    }
  }

  animationFrame = window.requestAnimationFrame(tick);
}

onMounted(() => animateTo(targetRate.value));
watch(targetRate, (nextValue) => animateTo(nextValue));
onBeforeUnmount(cancelAnimation);
</script>

<template>
  <div class="relative grid size-44 place-items-center" :aria-label="`${label} ${targetLabel}`">
    <svg class="size-44 -rotate-90 overflow-visible" viewBox="0 0 192 192" aria-hidden="true">
      <circle
        cx="96"
        cy="96"
        :r="radius"
        fill="none"
        class="stroke-slate-200/80 dark:stroke-white/10"
        stroke-width="16"
      />
      <circle
        cx="96"
        cy="96"
        :r="radius"
        fill="none"
        class="stroke-emerald-500"
        stroke-width="16"
        stroke-linecap="round"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="strokeOffset"
      />
    </svg>
    <div class="absolute inset-0 grid place-items-center">
      <div class="grid size-32 place-items-center rounded-full bg-white text-center shadow-inner dark:bg-[#171a21]">
        <div>
          <div class="text-[34px] font-semibold tabular-nums" aria-hidden="true">{{ animatedLabel }}</div>
          <div class="mt-1 text-[12px] text-slate-500">正常</div>
        </div>
      </div>
    </div>
  </div>
</template>
