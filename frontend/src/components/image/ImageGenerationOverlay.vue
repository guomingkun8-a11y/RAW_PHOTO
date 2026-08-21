<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { X } from "@lucide/vue";

const props = withDefaults(defineProps<{
  label?: string;
}>(), {
  label: "正在创建图片",
});

const emit = defineEmits<{
  close: [];
}>();

const canvasRef = ref<HTMLCanvasElement | null>(null);
const previewRef = ref<HTMLDivElement | null>(null);

type Cell = {
  column: number;
  row: number;
  threshold: number;
  noise: number;
};

let animationFrame = 0;
let resizeObserver: ResizeObserver | null = null;
let reducedMotionQuery: MediaQueryList | null = null;
let prefersReducedMotion = false;
let cells: Cell[] = [];
let geometry = {
  width: 0,
  height: 0,
  grid: 6,
  columns: 0,
  rows: 0,
};

const CYCLE_MS = 6000;

function buildCells(width: number, height: number) {
  const grid = Math.max(5, Math.round(Math.min(width, height) / 40));
  const columns = Math.ceil(width / grid);
  const rows = Math.ceil(height / grid);
  const centerX = columns / 2;
  const centerY = rows / 2;
  const maxDistance = Math.sqrt(centerX * centerX + centerY * centerY) || 1;
  const nextCells: Cell[] = [];

  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const distance = Math.sqrt((column - centerX) ** 2 + (row - centerY) ** 2) / maxDistance;
      nextCells.push({
        column,
        row,
        threshold: distance * 0.88 + Math.random() * 0.12,
        noise: Math.random(),
      });
    }
  }

  cells = nextCells;
  geometry = { width, height, grid, columns, rows };
}

function resizeCanvas() {
  const canvas = canvasRef.value;
  const preview = previewRef.value;
  if (!canvas || !preview) return;

  const rect = preview.getBoundingClientRect();
  const width = Math.max(96, Math.round(rect.width || 240));
  const height = Math.max(96, Math.round(rect.height || width));
  const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const nextWidth = Math.round(width * dpr);
  const nextHeight = Math.round(height * dpr);

  if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
    canvas.width = nextWidth;
    canvas.height = nextHeight;
  }

  const context = canvas.getContext("2d");
  if (!context) return;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);

  if (geometry.width !== width || geometry.height !== height) {
    buildCells(width, height);
  }

  paint(performance.now());
}

function paint(now: number) {
  const canvas = canvasRef.value;
  const context = canvas?.getContext("2d");
  if (!canvas || !context || !geometry.width || !geometry.height) return;

  const { width, height, grid, rows, columns } = geometry;
  const phase = prefersReducedMotion ? 0.76 : (now % CYCLE_MS) / CYCLE_MS;
  context.clearRect(0, 0, width, height);

  for (const cell of cells) {
    const rawProgress = (phase - cell.threshold * 0.7) / 0.35;
    const progress = Math.max(0, Math.min(1, rawProgress));
    if (progress <= 0) continue;

    const eased = 1 - (1 - progress) ** 3;
    const jitter = (Math.sin(now * 0.0015 + cell.noise * 8) * 0.015 + cell.noise * 0.025) * (1 - eased);
    const fill = Math.max(0, Math.min(1, eased + jitter));
    if (fill < 0.015) continue;

    const gray = Math.round(248 - fill * 48 + jitter * 30);
    context.fillStyle = `rgba(${gray}, ${gray}, ${gray}, ${(fill * 0.78).toFixed(2)})`;
    context.fillRect(cell.column * grid, cell.row * grid, grid - 0.5, grid - 0.5);
  }

  context.strokeStyle = "rgba(0, 0, 0, 0.018)";
  context.lineWidth = 0.35;
  for (let row = 0; row <= rows; row += 1) {
    context.beginPath();
    context.moveTo(0, row * grid);
    context.lineTo(width, row * grid);
    context.stroke();
  }
  for (let column = 0; column <= columns; column += 1) {
    context.beginPath();
    context.moveTo(column * grid, 0);
    context.lineTo(column * grid, height);
    context.stroke();
  }
}

function draw(now: number) {
  animationFrame = 0;
  paint(now);
  if (!prefersReducedMotion) {
    animationFrame = window.requestAnimationFrame(draw);
  }
}

function startAnimation() {
  if (!animationFrame && !prefersReducedMotion) {
    animationFrame = window.requestAnimationFrame(draw);
  }
}

function stopAnimation() {
  if (!animationFrame) return;
  window.cancelAnimationFrame(animationFrame);
  animationFrame = 0;
}

function handleReducedMotionChange(event: MediaQueryListEvent) {
  prefersReducedMotion = event.matches;
  if (prefersReducedMotion) {
    stopAnimation();
    paint(performance.now());
  } else {
    startAnimation();
  }
}

onMounted(() => {
  reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  prefersReducedMotion = reducedMotionQuery.matches;
  reducedMotionQuery.addEventListener("change", handleReducedMotionChange);

  resizeObserver = new ResizeObserver(() => resizeCanvas());
  if (previewRef.value) resizeObserver.observe(previewRef.value);
  resizeCanvas();
  startAnimation();
});

onBeforeUnmount(() => {
  stopAnimation();
  resizeObserver?.disconnect();
  reducedMotionQuery?.removeEventListener("change", handleReducedMotionChange);
});
</script>

<template>
  <div
    class="image-generation-overlay"
    role="status"
    aria-live="polite"
    aria-busy="true"
    data-testid="image-generation-overlay"
  >
    <div class="generation-overlay-header">
      <span class="generation-overlay-title">
        <span class="generation-state-mark" aria-hidden="true" />
        {{ props.label }}
      </span>
      <button
        type="button"
        class="generation-overlay-close"
        aria-label="停止生成"
        title="停止生成"
        @click="emit('close')"
      >
        <X class="size-4" />
      </button>
    </div>

    <div ref="previewRef" class="generation-preview" aria-hidden="true">
      <canvas ref="canvasRef" class="generation-canvas" />
    </div>

    <div class="generation-thinking" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  </div>
</template>

<style scoped>
.image-generation-overlay {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 100%;
  isolation: isolate;
  overflow: hidden;
  contain: layout paint;
  border-radius: inherit;
  background: #fff;
  color: #202123;
}

.generation-overlay-header {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 11px 0 12px;
}

.generation-overlay-title {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  max-width: calc(100% - 38px);
  border-radius: 999px;
  background: rgb(255 255 255 / 0.72);
  padding: 0.28rem 0.5rem;
  color: rgb(32 33 35 / 0.72);
  font-size: 12px;
  font-weight: 550;
  line-height: 1.35;
  backdrop-filter: blur(6px);
}

.generation-state-mark {
  width: 5px;
  height: 5px;
  flex: none;
  border-radius: 50%;
  background: #9ca3af;
  animation: generation-state-pulse 1.6s ease-in-out infinite;
}

.generation-overlay-close {
  display: inline-grid;
  width: 28px;
  height: 28px;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: rgb(32 33 35 / 0.5);
  cursor: pointer;
  transition:
    color 180ms var(--studio-ease),
    background-color 180ms var(--studio-ease),
    transform 120ms var(--studio-ease);
}

.generation-overlay-close:hover {
  background: rgb(32 33 35 / 0.055);
  color: rgb(32 33 35 / 0.86);
}

.generation-overlay-close:active {
  transform: scale(0.94);
}

.generation-overlay-close:focus-visible {
  outline: 2px solid rgb(79 124 255 / 0.42);
  outline-offset: 2px;
}

.generation-preview {
  position: absolute;
  inset: 0;
  z-index: 1;
  overflow: hidden;
  border-radius: inherit;
  background: #f7f7f8;
  box-shadow: inset 0 0 0 1px rgb(0 0 0 / 0.035);
}

.generation-canvas {
  display: block;
  width: 100%;
  height: 100%;
}

.generation-thinking {
  position: absolute;
  left: 50%;
  bottom: 12px;
  z-index: 3;
  display: flex;
  height: 10px;
  align-items: center;
  gap: 5px;
  transform: translateX(-50%);
}

.generation-thinking span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #b4b4b4;
  animation: generation-dot-pulse 1.4s ease-in-out infinite;
}

.generation-thinking span:nth-child(2) {
  animation-delay: 0.2s;
}

.generation-thinking span:nth-child(3) {
  animation-delay: 0.4s;
}

:global(.dark) .image-generation-overlay {
  background: #111317;
  color: rgb(244 244 245 / 0.9);
}

:global(.dark) .generation-overlay-title {
  background: rgb(17 19 23 / 0.68);
  color: rgb(244 244 245 / 0.7);
}

:global(.dark) .generation-state-mark,
:global(.dark) .generation-thinking span {
  background: #9ca3af;
}

:global(.dark) .generation-overlay-close {
  color: rgb(244 244 245 / 0.48);
}

:global(.dark) .generation-overlay-close:hover {
  background: rgb(255 255 255 / 0.08);
  color: rgb(244 244 245 / 0.9);
}

:global(.dark) .generation-preview {
  background: #f7f7f8;
}

@keyframes generation-dot-pulse {
  0%,
  80%,
  100% {
    opacity: 0.35;
    transform: scale(0.4);
  }

  40% {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes generation-state-pulse {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(0.72);
  }

  50% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 640px) {
  .generation-overlay-header {
    padding: 10px 10px 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .generation-state-mark,
  .generation-thinking span {
    animation: none;
  }

  .generation-state-mark,
  .generation-thinking span {
    opacity: 0.68;
    transform: scale(0.72);
  }
}
</style>
