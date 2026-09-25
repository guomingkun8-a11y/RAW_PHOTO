<script setup lang="ts">
import { Map as MapIcon, Minimize2 } from '@lucide/vue';
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { CanvasViewport } from '@/features/infinite-canvas/composables/useCanvasViewport';

const MINIMAP_COLLAPSED_STORAGE_KEY = 'gmkraw-canvas-minimap-collapsed';

export interface CanvasMinimapNode {
  id: string;
  type: 'text' | 'image' | 'video';
  x: number;
  y: number;
  width: number;
  height: number;
}

const props = defineProps<{
  nodes: CanvasMinimapNode[];
  viewport: CanvasViewport;
  surfaceWidth: number;
  surfaceHeight: number;
  selectedIds: string[];
  compact?: boolean;
}>();

const emit = defineEmits<{
  navigate: [point: { x: number; y: number }];
}>();

const canvas = ref<HTMLCanvasElement>();
const collapsed = ref(Boolean(props.compact) || readCollapsedPreference());
const navigating = ref(false);
let drawFrame: number | undefined;
let mapBounds = { left: 0, top: 0, right: 1, bottom: 1 };
let mapTransform = { scale: 1, offsetX: 0, offsetY: 0 };
let navigationOffset = { x: 0, y: 0 };
let themeObserver: MutationObserver | undefined;

function readCollapsedPreference() {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(MINIMAP_COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function saveCollapsedPreference(value: boolean) {
  try {
    window.localStorage.setItem(MINIMAP_COLLAPSED_STORAGE_KEY, String(value));
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function calculateBounds() {
  const zoom = Math.max(props.viewport.zoom, 0.01);
  const viewportBounds = {
    left: -props.viewport.x / zoom,
    top: -props.viewport.y / zoom,
    right: (props.surfaceWidth - props.viewport.x) / zoom,
    bottom: (props.surfaceHeight - props.viewport.y) / zoom,
  };
  const left = Math.min(viewportBounds.left, ...props.nodes.map((node) => node.x));
  const top = Math.min(viewportBounds.top, ...props.nodes.map((node) => node.y));
  const right = Math.max(viewportBounds.right, ...props.nodes.map((node) => node.x + node.width));
  const bottom = Math.max(viewportBounds.bottom, ...props.nodes.map((node) => node.y + node.height));
  const padding = Math.max(80, Math.min(300, Math.max(right - left, bottom - top) * 0.05));
  return { left: left - padding, top: top - padding, right: right + padding, bottom: bottom + padding };
}

function draw() {
  drawFrame = undefined;
  const element = canvas.value;
  if (!element || collapsed.value) return;
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (!width || !height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  element.width = Math.round(width * ratio);
  element.height = Math.round(height * ratio);
  const context = element.getContext('2d');
  if (!context) return;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  mapBounds = calculateBounds();
  const worldWidth = Math.max(1, mapBounds.right - mapBounds.left);
  const worldHeight = Math.max(1, mapBounds.bottom - mapBounds.top);
  const inset = 9;
  const scale = Math.min((width - inset * 2) / worldWidth, (height - inset * 2) / worldHeight);
  const offsetX = (width - worldWidth * scale) / 2;
  const offsetY = (height - worldHeight * scale) / 2;
  mapTransform = { scale, offsetX, offsetY };
  const selected = new Set(props.selectedIds);
  const styles = getComputedStyle(element);
  const imageColor = styles.getPropertyValue('--minimap-image').trim() || '#5b7ff5';
  const videoColor = styles.getPropertyValue('--minimap-video').trim() || '#299b7e';
  const textColor = styles.getPropertyValue('--minimap-text').trim() || '#8791a2';
  const nodeStrokeColor = styles.getPropertyValue('--minimap-node-stroke').trim() || 'rgba(30, 41, 59, 0.16)';
  const accentColor = styles.getPropertyValue('--accent').trim() || '#4f7cff';
  const viewportFill = styles.getPropertyValue('--minimap-viewport-fill').trim() || 'rgba(79, 124, 255, 0.12)';
  const mapSurface = styles.getPropertyValue('--minimap-surface').trim() || '#f7f8fa';
  const drawingContext = context;

  context.globalAlpha = 1;
  context.fillStyle = mapSurface;
  context.fillRect(0, 0, width, height);

  function roundedRect(x: number, y: number, rectWidth: number, rectHeight: number, radius: number) {
    drawingContext.beginPath();
    drawingContext.roundRect(x, y, rectWidth, rectHeight, Math.min(radius, rectWidth / 2, rectHeight / 2));
  }

  for (const node of props.nodes) {
    const x = offsetX + (node.x - mapBounds.left) * scale;
    const y = offsetY + (node.y - mapBounds.top) * scale;
    const nodeWidth = Math.max(3.5, node.width * scale);
    const nodeHeight = Math.max(3, node.height * scale);
    const selectedNode = selected.has(node.id);
    context.fillStyle = node.type === 'image'
      ? imageColor
      : node.type === 'video'
        ? videoColor
        : textColor;
    context.globalAlpha = selectedNode ? 1 : 0.78;
    roundedRect(x, y, nodeWidth, nodeHeight, 2.5);
    context.fill();
    context.globalAlpha = selectedNode ? 1 : 0.46;
    context.strokeStyle = selectedNode ? accentColor : nodeStrokeColor;
    context.lineWidth = selectedNode ? 1.5 : 0.75;
    context.stroke();
  }

  const zoom = Math.max(props.viewport.zoom, 0.01);
  const viewportLeft = -props.viewport.x / zoom;
  const viewportTop = -props.viewport.y / zoom;
  const viewportWidth = props.surfaceWidth / zoom;
  const viewportHeight = props.surfaceHeight / zoom;
  context.globalAlpha = 1;
  const viewportX = offsetX + (viewportLeft - mapBounds.left) * scale;
  const viewportY = offsetY + (viewportTop - mapBounds.top) * scale;
  const viewportMapWidth = Math.max(8, viewportWidth * scale);
  const viewportMapHeight = Math.max(6, viewportHeight * scale);
  context.fillStyle = viewportFill;
  context.globalAlpha = 1;
  roundedRect(viewportX, viewportY, viewportMapWidth, viewportMapHeight, 3);
  context.fill();
  context.strokeStyle = accentColor;
  context.lineWidth = 1.5;
  context.stroke();
  context.globalAlpha = 1;
}

function scheduleDraw() {
  if (drawFrame !== undefined) return;
  drawFrame = window.requestAnimationFrame(draw);
}

function minimapPoint(event: PointerEvent) {
  const element = canvas.value;
  if (!element) return undefined;
  const bounds = element.getBoundingClientRect();
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
}

function currentViewportRect() {
  const zoom = Math.max(props.viewport.zoom, 0.01);
  return {
    x: mapTransform.offsetX + (-props.viewport.x / zoom - mapBounds.left) * mapTransform.scale,
    y: mapTransform.offsetY + (-props.viewport.y / zoom - mapBounds.top) * mapTransform.scale,
    width: Math.max(8, props.surfaceWidth / zoom * mapTransform.scale),
    height: Math.max(6, props.surfaceHeight / zoom * mapTransform.scale),
  };
}

function navigate(event: PointerEvent) {
  const point = minimapPoint(event);
  if (!point) return;
  emit('navigate', {
    x: mapBounds.left + (point.x - navigationOffset.x - mapTransform.offsetX) / mapTransform.scale,
    y: mapBounds.top + (point.y - navigationOffset.y - mapTransform.offsetY) / mapTransform.scale,
  });
}

function beginNavigation(event: PointerEvent) {
  if (event.button !== 0) return;
  const point = minimapPoint(event);
  if (!point) return;
  const viewportRect = currentViewportRect();
  const insideViewport = point.x >= viewportRect.x
    && point.x <= viewportRect.x + viewportRect.width
    && point.y >= viewportRect.y
    && point.y <= viewportRect.y + viewportRect.height;
  navigationOffset = insideViewport
    ? {
      x: point.x - (viewportRect.x + viewportRect.width / 2),
      y: point.y - (viewportRect.y + viewportRect.height / 2),
    }
    : { x: 0, y: 0 };
  navigating.value = true;
  canvas.value?.setPointerCapture(event.pointerId);
  navigate(event);
}

function moveNavigation(event: PointerEvent) {
  if (navigating.value) navigate(event);
}

function endNavigation(event: PointerEvent) {
  navigating.value = false;
  navigationOffset = { x: 0, y: 0 };
  if (canvas.value?.hasPointerCapture(event.pointerId)) canvas.value.releasePointerCapture(event.pointerId);
}

function navigateWithKeyboard(event: KeyboardEvent) {
  const zoom = Math.max(props.viewport.zoom, 0.01);
  const center = {
    x: (-props.viewport.x + props.surfaceWidth / 2) / zoom,
    y: (-props.viewport.y + props.surfaceHeight / 2) / zoom,
  };
  const stepX = props.surfaceWidth / zoom * (event.shiftKey ? 0.5 : 0.16);
  const stepY = props.surfaceHeight / zoom * (event.shiftKey ? 0.5 : 0.16);
  if (event.key === 'ArrowLeft') center.x -= stepX;
  else if (event.key === 'ArrowRight') center.x += stepX;
  else if (event.key === 'ArrowUp') center.y -= stepY;
  else if (event.key === 'ArrowDown') center.y += stepY;
  else return;
  event.preventDefault();
  emit('navigate', center);
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  saveCollapsedPreference(collapsed.value);
  if (!collapsed.value) void nextTick(scheduleDraw);
}

watch(() => props.nodes, scheduleDraw);
watch(() => props.viewport, scheduleDraw);
watch(() => [props.surfaceWidth, props.surfaceHeight, props.selectedIds.join('|')], scheduleDraw);
watch(() => props.compact, (value) => {
  if (value) collapsed.value = true;
});
onMounted(scheduleDraw);
onBeforeUnmount(() => {
  if (drawFrame !== undefined) window.cancelAnimationFrame(drawFrame);
  themeObserver?.disconnect();
});

onMounted(() => {
  themeObserver = new MutationObserver(scheduleDraw);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
});
</script>

<template>
  <aside
    class="canvas-minimap"
    :class="{ 'is-collapsed': collapsed, 'is-navigating': navigating }"
    :aria-label="`画布概览，共 ${nodes.length} 个节点`"
    @pointerdown.stop
    @contextmenu.stop.prevent
  >
    <button
      v-if="collapsed"
      class="canvas-minimap-trigger"
      type="button"
      title="展开小地图"
      aria-label="展开小地图"
      :aria-expanded="false"
      @click="toggleCollapsed"
    >
      <MapIcon :size="16" aria-hidden="true" />
    </button>
    <div v-else class="canvas-minimap-shell">
      <canvas
        ref="canvas"
        class="canvas-minimap-map"
        tabindex="0"
        aria-label="画布小地图，可点击或拖动导航，方向键可以移动视口"
        @keydown="navigateWithKeyboard"
        @pointerdown="beginNavigation"
        @pointermove="moveNavigation"
        @pointerup="endNavigation"
        @pointercancel="endNavigation"
      />
      <button
        class="canvas-minimap-collapse"
        type="button"
        title="收起小地图"
        aria-label="收起小地图"
        :aria-expanded="true"
        @click="toggleCollapsed"
      >
        <Minimize2 :size="14" aria-hidden="true" />
      </button>
    </div>
  </aside>
</template>
