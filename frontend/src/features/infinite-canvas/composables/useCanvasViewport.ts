import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  toRaw,
  type Ref,
} from 'vue';

export interface CanvasViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface CanvasWorldBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface PositionedNode {
  id: string;
  x: number;
  y: number;
}

interface UseCanvasViewportOptions<T extends PositionedNode> {
  canvas: Ref<HTMLElement | undefined>;
  nodes: Ref<T[]>;
  viewport: Ref<CanvasViewport>;
  nodeRevision: Ref<number>;
  getNodeSize: (node: T) => { width: number; height: number };
  keepNodeIds?: () => Iterable<string>;
  minZoom: number;
  maxZoom: number;
  overscanPixels?: number;
  spatialCellSize?: number;
  onViewportChange?: () => void;
}

function intersects(left: CanvasWorldBounds, right: CanvasWorldBounds) {
  return left.left <= right.right
    && left.right >= right.left
    && left.top <= right.bottom
    && left.bottom >= right.top;
}

export function useCanvasViewport<T extends PositionedNode>(options: UseCanvasViewportOptions<T>) {
  const surfaceWidth = ref(0);
  const surfaceHeight = ref(0);
  const overscanPixels = options.overscanPixels ?? 560;
  const cellSize = options.spatialCellSize ?? 900;
  let resizeObserver: ResizeObserver | undefined;

  function updateSurfaceSize() {
    const bounds = options.canvas.value?.getBoundingClientRect();
    surfaceWidth.value = bounds?.width || 0;
    surfaceHeight.value = bounds?.height || 0;
  }

  onMounted(() => {
    updateSurfaceSize();
    if (!options.canvas.value || typeof ResizeObserver === 'undefined') return;
    resizeObserver = new ResizeObserver(updateSurfaceSize);
    resizeObserver.observe(options.canvas.value);
  });

  onBeforeUnmount(() => resizeObserver?.disconnect());

  const worldViewportBounds = computed<CanvasWorldBounds>(() => {
    const { x, y, zoom } = options.viewport.value;
    const safeZoom = Math.max(zoom, 0.01);
    const overscan = overscanPixels / safeZoom;
    return {
      left: -x / safeZoom - overscan,
      top: -y / safeZoom - overscan,
      right: (surfaceWidth.value - x) / safeZoom + overscan,
      bottom: (surfaceHeight.value - y) / safeZoom + overscan,
    };
  });

  const spatialIndex = computed(() => {
    options.nodeRevision.value;
    const cells = new Map<string, string[]>();
    const boundsById = new Map<string, CanvasWorldBounds>();
    const nodesById = new Map<string, T>();
    const orderById = new Map<string, number>();
    const source = toRaw(options.nodes.value) as T[];
    for (let index = 0; index < source.length; index += 1) {
      const node = source[index];
      const reactiveNode = options.nodes.value[index];
      const size = options.getNodeSize(node);
      const bounds = {
        left: node.x,
        top: node.y,
        right: node.x + size.width,
        bottom: node.y + size.height,
      };
      boundsById.set(node.id, bounds);
      nodesById.set(node.id, reactiveNode);
      orderById.set(node.id, index);
      const minColumn = Math.floor(bounds.left / cellSize);
      const maxColumn = Math.floor(bounds.right / cellSize);
      const minRow = Math.floor(bounds.top / cellSize);
      const maxRow = Math.floor(bounds.bottom / cellSize);
      for (let column = minColumn; column <= maxColumn; column += 1) {
        for (let row = minRow; row <= maxRow; row += 1) {
          const key = `${column}:${row}`;
          const ids = cells.get(key);
          if (ids) ids.push(node.id);
          else cells.set(key, [node.id]);
        }
      }
    }
    return { cells, boundsById, nodesById, orderById };
  });

  const visibleNodeIds = computed(() => {
    const viewportBounds = worldViewportBounds.value;
    const candidates = new Set<string>();
    const keepIds = new Set(options.keepNodeIds?.() || []);
    const minColumn = Math.floor(viewportBounds.left / cellSize);
    const maxColumn = Math.floor(viewportBounds.right / cellSize);
    const minRow = Math.floor(viewportBounds.top / cellSize);
    const maxRow = Math.floor(viewportBounds.bottom / cellSize);
    for (let column = minColumn; column <= maxColumn; column += 1) {
      for (let row = minRow; row <= maxRow; row += 1) {
        for (const id of spatialIndex.value.cells.get(`${column}:${row}`) || []) candidates.add(id);
      }
    }
    for (const id of keepIds) candidates.add(id);
    return new Set([...candidates].filter((id) => {
      if (keepIds.has(id)) return true;
      const bounds = spatialIndex.value.boundsById.get(id);
      return Boolean(bounds && intersects(bounds, viewportBounds));
    }));
  });

  const visibleNodes = computed(() => [...visibleNodeIds.value]
    .sort((left, right) => (
      (spatialIndex.value.orderById.get(left) || 0) - (spatialIndex.value.orderById.get(right) || 0)
    ))
    .flatMap((id) => {
      const node = spatialIndex.value.nodesById.get(id);
      return node ? [node] : [];
    }));

  function boundsForNodeIds(ids?: Iterable<string>) {
    const requested = ids ? new Set(ids) : undefined;
    const boxes = [...spatialIndex.value.boundsById.entries()]
      .filter(([id]) => !requested || requested.has(id))
      .map(([, bounds]) => bounds);
    if (!boxes.length) return undefined;
    return {
      left: Math.min(...boxes.map((bounds) => bounds.left)),
      top: Math.min(...boxes.map((bounds) => bounds.top)),
      right: Math.max(...boxes.map((bounds) => bounds.right)),
      bottom: Math.max(...boxes.map((bounds) => bounds.bottom)),
    };
  }

  async function fitNodes(ids?: Iterable<string>) {
    await nextTick();
    updateSurfaceSize();
    const bounds = boundsForNodeIds(ids);
    if (!bounds || !surfaceWidth.value || !surfaceHeight.value) return false;
    const padding = Math.min(96, Math.max(40, Math.min(surfaceWidth.value, surfaceHeight.value) * 0.08));
    const contentWidth = Math.max(1, bounds.right - bounds.left);
    const contentHeight = Math.max(1, bounds.bottom - bounds.top);
    const zoom = Math.max(options.minZoom, Math.min(
      options.maxZoom,
      1.15,
      (surfaceWidth.value - padding * 2) / contentWidth,
      (surfaceHeight.value - padding * 2) / contentHeight,
    ));
    options.viewport.value = {
      zoom,
      x: surfaceWidth.value / 2 - (bounds.left + contentWidth / 2) * zoom,
      y: surfaceHeight.value / 2 - (bounds.top + contentHeight / 2) * zoom,
    };
    options.onViewportChange?.();
    return true;
  }

  async function locateNode(nodeId?: string) {
    if (!nodeId) return false;
    await nextTick();
    updateSurfaceSize();
    const bounds = spatialIndex.value.boundsById.get(nodeId);
    if (!bounds || !surfaceWidth.value || !surfaceHeight.value) return false;
    const width = Math.max(1, bounds.right - bounds.left);
    const height = Math.max(1, bounds.bottom - bounds.top);
    const zoom = Math.max(0.55, Math.min(
      1.15,
      options.maxZoom,
      (surfaceWidth.value - 96) / width,
      (surfaceHeight.value - 96) / height,
    ));
    options.viewport.value = {
      zoom,
      x: surfaceWidth.value / 2 - (bounds.left + width / 2) * zoom,
      y: surfaceHeight.value / 2 - (bounds.top + height / 2) * zoom,
    };
    options.onViewportChange?.();
    return true;
  }

  function navigateToWorld(x: number, y: number) {
    options.viewport.value = {
      ...options.viewport.value,
      x: surfaceWidth.value / 2 - x * options.viewport.value.zoom,
      y: surfaceHeight.value / 2 - y * options.viewport.value.zoom,
    };
    options.onViewportChange?.();
  }

  return {
    surfaceWidth,
    surfaceHeight,
    worldViewportBounds,
    visibleNodeIds,
    visibleNodes,
    fitNodes,
    locateNode,
    navigateToWorld,
    updateSurfaceSize,
  };
}
