<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  ArrowLeft,
  Clapperboard,
  Copy,
  FileText,
  FolderOpen,
  GitBranch,
  Image as ImageIcon,
  Link2,
  LoaderCircle,
  Plus,
  Redo2,
  Save,
  Scissors,
  Sparkles,
  Film,
  Trash2,
  Undo2,
  Video,
} from '@lucide/vue';

import {
  cancelImageTask,
  cancelVideoGenerationTask,
  type AgentVideoAsset,
  type VideoGenerationModelSpec,
} from '@/lib/api';
import { HttpRequestError } from '@/lib/request';
import { BUILTIN_IMAGE_MODELS } from '@/lib/image-models';
import { videoFrameRole, videoModelUsesFirstLastFrames } from '@/lib/video-models';
import CanvasAssistantDock from '@/features/infinite-canvas/components/CanvasAssistantDock.vue';
import CanvasMinimap from '@/features/infinite-canvas/components/CanvasMinimap.vue';
import CanvasNode from '@/features/infinite-canvas/components/CanvasNode.vue';
import CanvasToolbar from '@/features/infinite-canvas/components/CanvasToolbar.vue';
import CanvasWorkflowPanel from '@/features/infinite-canvas/components/CanvasWorkflowPanel.vue';
import StoryboardDialog from '@/features/infinite-canvas/components/StoryboardDialog.vue';
import { useCanvasAutosave } from '@/features/infinite-canvas/composables/useCanvasAutosave';
import { useCanvasHistory } from '@/features/infinite-canvas/composables/useCanvasHistory';
import { useCanvasSelection } from '@/features/infinite-canvas/composables/useCanvasSelection';
import {
  useCanvasViewport,
  type CanvasViewport,
} from '@/features/infinite-canvas/composables/useCanvasViewport';
import VideoTimelineWorkbench from '@/features/video-timeline/components/VideoTimelineWorkbench.vue';
import {
  canvasApi,
  downloadCanvasImage,
  downloadCanvasImages,
  fetchCanvasVideoAnalysisStatuses,
  generateCanvasImage,
  generateCanvasVideo,
  loadCanvasRuntime,
  retryCanvasVideoAnalysis,
  resumeCanvasGeneration,
  uploadCanvasImage,
  uploadCanvasVideo,
} from '@/features/infinite-canvas/services/canvas-api';
import type {
  CanvasModelOptions,
  CanvasConnection,
  CanvasMediaReference,
  CanvasNode as CanvasNodeModel,
  Capabilities,
  ChatMessage,
  NodeKind,
  ReferenceImage,
  WorkflowDocument,
  WorkflowSummary,
} from '@/features/infinite-canvas/types';
import {
  ACTIVE_VIDEO_ANALYSIS_STATUSES,
  normalizeCanvasVideoAnalysis,
  normalizeVideoAnalysisStatus,
  videoAnalysisPromptLines,
} from '@/features/infinite-canvas/utils/video-analysis';
import type { CompositionTask, TimelineDocument } from '@/features/video-timeline/types/timeline';

const IMAGE_NODE_WIDTH = 440;
const VIDEO_NODE_WIDTH = 620;
const MEDIA_NODE_HEADER_HEIGHT = 38;
const IMAGE_NODE_DEFAULT_STAGE_HEIGHT = IMAGE_NODE_WIDTH;
const IMAGE_NODE_MAX_STAGE_HEIGHT = 800;
const VIDEO_NODE_DEFAULT_STAGE_HEIGHT = 330;
const MEDIA_NODE_PROMPT_GAP = 14;
const MEDIA_NODE_PROMPT_WIDTH = 620;
const MEDIA_NODE_PROMPT_HEIGHT = 218;
const MEDIA_NODE_EXPANDED_CARD_WIDTH = IMAGE_NODE_WIDTH;
const MEDIA_NODE_EXPANDED_GAP = 10;
const MEDIA_NODE_EXPANDED_MAX_WIDTH = 1340;
const TEXT_NODE_WIDTH = 300;
const TEXT_NODE_HEIGHT = 220;
const GRID_SIZE = 26;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 2.5;
const COMPACT_VIEWPORT_QUERY = '(max-width: 800px)';
const CONNECTION_DELETE_HOVER_DELAY_MS = 650;
const CONNECTION_DELETE_LEAVE_DELAY_MS = 220;
const VIDEO_ANALYSIS_POLL_INTERVAL_MS = 3_000;

interface NodeDragState {
  id: string;
  clientX: number;
  clientY: number;
  origins: Map<string, { x: number; y: number }>;
}

interface PanState {
  clientX: number;
  clientY: number;
  x: number;
  y: number;
}

interface ConnectionLine {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface ConnectionPreview {
  sourceId: string;
  x: number;
  y: number;
}

interface ConnectionTargetPoint {
  nodeId: string;
  offset: number;
}

interface ConnectionDeleteControl {
  id: string;
  x: number;
  y: number;
}

interface ContextMenuState {
  x: number;
  y: number;
  nodeId?: string;
  connectionId?: string;
  worldX?: number;
  worldY?: number;
}

interface CanvasHistorySnapshot {
  nodes: CanvasNodeModel[];
  connections: CanvasConnection[];
}

interface SelectionBoxState {
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
  additive: boolean;
  initialIds: Set<string>;
}

interface CanvasClipboard {
  nodes: CanvasNodeModel[];
  connections: CanvasConnection[];
  pasteCount: number;
}

const compactViewport = typeof window === 'undefined'
  ? undefined
  : window.matchMedia(COMPACT_VIEWPORT_QUERY);

function defaultViewport(): CanvasViewport {
  return compactViewport?.matches
    ? { x: -100, y: 130, zoom: 1 }
    : { x: 160, y: 130, zoom: 1 };
}

const canvas = ref<HTMLElement>();
const nodes = ref<CanvasNodeModel[]>([]);
const connections = ref<CanvasConnection[]>([]);
const {
  selectedIds,
  selectedId,
  selectionCount,
  hasSelection,
  selectOnly,
  toggleSelection,
  selectMany,
  clearSelection,
  removeFromSelection,
  retainSelection,
  isSelected,
} = useCanvasSelection();
const connectionSourceId = ref<string>();
const connectionSourceImageIndex = ref<number>();
const connectionTargetId = ref<string>();
const connectionTargetPoint = ref<ConnectionTargetPoint>();
const connectionPreview = ref<ConnectionPreview>();
const connectionPointerActive = ref(false);
const viewport = ref<CanvasViewport>(defaultViewport());
const nodeDrag = ref<NodeDragState>();
const selectionBox = ref<SelectionBoxState>();
const canvasClipboard = ref<CanvasClipboard>();
const canvasDocumentRevision = ref(0);
const contextMenu = ref<ContextMenuState>();
const connectionDeleteControl = ref<ConnectionDeleteControl>();
const panState = ref<PanState>();
const assistantOpen = ref(false);
const workflowPanelOpen = ref(false);
const workflowTitle = ref('未命名画布');
const activeWorkflowId = ref<string>(makeId('workflow'));
const workflowRevision = ref(0);
const workflowCreatedAt = ref<string>();
const capabilities = ref<Capabilities>({
  chat: false,
  image: false,
  video: false,
  referenceImageUpload: false,
  videoUpload: false,
});
const modelOptions = ref<CanvasModelOptions>({
  imageModel: 'gpt-image-2',
  imageModels: [...BUILTIN_IMAGE_MODELS],
  videoModels: [],
  textVideoModel: '',
  imageVideoModel: '',
  textVideoQuality: 'standard',
  imageVideoQuality: 'standard',
});
const backendOnline = ref(false);
const toast = ref('');
const storyboardOpen = ref(false);
const timelineOpen = ref(false);
const timeline = ref<TimelineDocument>();
const latestComposition = ref<CompositionTask>();

const loadingWorkflows = ref(false);
const deletingWorkflows = ref(false);
const workflows = ref<WorkflowSummary[]>([]);
const chatMessages = ref<ChatMessage[]>([]);
const chatInput = ref('');
const chatSending = ref(false);
const taskControllers = new Map<string, AbortController>();
const deletingNodeIds = new Set<string>();
const downloadingNodeId = ref<string>();
const downloadingImageKey = ref<string>();
const retryingVideoAnalysisIds = ref<Set<string>>(new Set());
let pointerMoveFrame: number | undefined;
let pendingPointer: { clientX: number; clientY: number } | undefined;
let pendingConnectionHover: ConnectionDeleteControl | undefined;
let connectionHoverTimer: number | undefined;
let connectionHideTimer: number | undefined;
let viewportDirtyTimer: number | undefined;
let videoAnalysisPollTimer: number | undefined;
let videoAnalysisPollInFlight = false;
let videoAnalysisPollEpoch = 0;
let workflowLoadEpoch = 0;

const {
  dirty: workflowDirty,
  saving: savingWorkflow,
  lastError: workflowSaveError,
  lastSavedAt: workflowLastSavedAt,
  blocked: workflowSaveBlocked,
  markDirty: markWorkflowDirty,
  saveNow: saveWorkflowNow,
  flush: flushWorkflowSave,
  pause: pauseWorkflowAutosave,
  resume: resumeWorkflowAutosave,
  resetContext: resetWorkflowAutosaveContext,
  resumeAsSaved: resumeWorkflowAsSaved,
} = useCanvasAutosave<WorkflowDocument>({
  capture: workflowSnapshot,
  persist: persistWorkflowSnapshot,
  delayMs: 900,
  maxWaitMs: 5_000,
  retryDelayMs: 5_000,
  initialPaused: true,
  isTerminalError: (error) => error instanceof HttpRequestError && [405, 409].includes(error.status || 0),
});

const {
  canUndo: canUndoCanvas,
  canRedo: canRedoCanvas,
  commit: commitCanvasHistorySnapshot,
  scheduleCommit: scheduleCanvasHistorySnapshot,
  replaceCurrent: replaceCanvasHistorySnapshot,
  reset: resetCanvasHistory,
  undo: undoCanvasHistory,
  redo: redoCanvasHistory,
} = useCanvasHistory<CanvasHistorySnapshot>({
  capture: captureCanvasHistory,
  restore: restoreCanvasHistory,
  limit: 50,
  debounceMs: 450,
});

watch([workflowTitle, chatMessages, timeline, latestComposition], markWorkflowDirty);

function touchCanvasDocument() {
  canvasDocumentRevision.value += 1;
  markWorkflowDirty();
}

function scheduleViewportDirty() {
  if (viewportDirtyTimer !== undefined) window.clearTimeout(viewportDirtyTimer);
  viewportDirtyTimer = window.setTimeout(() => {
    viewportDirtyTimer = undefined;
    markWorkflowDirty();
  }, 180);
}

function commitCanvasHistory() {
  touchCanvasDocument();
  return commitCanvasHistorySnapshot();
}

function scheduleCanvasHistoryCommit() {
  touchCanvasDocument();
  scheduleCanvasHistorySnapshot();
}

function replaceCanvasHistoryCurrent() {
  touchCanvasDocument();
  replaceCanvasHistorySnapshot();
}

const workflowSaveLabel = computed(() => {
  if (savingWorkflow.value) return '保存中';
  if (workflowSaveError.value) return '保存失败';
  if (workflowDirty.value) return '未保存';
  return workflowLastSavedAt.value ? '已保存' : '尚未保存';
});

const workflowSaveTitle = computed(() => workflowSaveError.value
  ? `保存失败：${workflowSaveError.value}`
  : workflowSaveLabel.value);

function captureCanvasHistory(): CanvasHistorySnapshot {
  return JSON.parse(JSON.stringify({
    nodes: nodes.value,
    connections: connections.value,
  })) as CanvasHistorySnapshot;
}

function restoreCanvasHistory(snapshot: CanvasHistorySnapshot) {
  nodes.value = snapshot.nodes.map((item) => {
    const node = normalizeCanvasNode(item);
    if (node.status === 'generating' || node.status === 'uploading') {
      node.status = 'failed';
      node.progress = undefined;
      node.error = '已恢复节点配置，运行中的任务不会通过撤销重新启动。';
    }
    return node;
  });
  const nodeIds = new Set(nodes.value.map((node) => node.id));
  connections.value = snapshot.connections
    .filter((connection) => nodeIds.has(connection.from) && nodeIds.has(connection.to))
    .map((connection) => ({ ...connection }));
  retainSelection(nodeIds);
  canvasDocumentRevision.value += 1;
  clearConnectionState();
  clearConnectionDeleteControl();
  closeContextMenu();
  scheduleVideoAnalysisPolling(250);
}

function hasActiveCanvasOperations() {
  return nodes.value.some((node) => node.status === 'generating' || node.status === 'uploading');
}

function undoCanvasChange() {
  if (hasActiveCanvasOperations()) {
    showToast('生成或上传进行中，完成后再撤销。');
    return;
  }
  if (undoCanvasHistory()) touchCanvasDocument();
}

function redoCanvasChange() {
  if (hasActiveCanvasOperations()) {
    showToast('生成或上传进行中，完成后再恢复。');
    return;
  }
  if (redoCanvasHistory()) touchCanvasDocument();
}

function imageResultUrls(node: CanvasNodeModel) {
  const values = node.resultUrls?.filter(Boolean) || [];
  if (!values.length && node.resultUrl) values.push(node.resultUrl);
  return values;
}

function primaryImageIndex(node: CanvasNodeModel) {
  const urls = imageResultUrls(node);
  if (!urls.length) return 0;
  const requested = Number(node.primaryImageIndex);
  return Number.isInteger(requested) && requested >= 0 && requested < urls.length ? requested : 0;
}

function normalizedImageIndex(node: CanvasNodeModel, value: unknown, fallback = primaryImageIndex(node)) {
  const maxIndex = Math.max(0, imageResultUrls(node).length - 1);
  const requested = Number(value);
  const hasRequestedValue = value !== undefined && value !== null && value !== '';
  const index = hasRequestedValue && Number.isInteger(requested) && requested >= 0 ? requested : fallback;
  return Math.min(Math.max(0, index), maxIndex);
}

function imageResultUrl(node: CanvasNodeModel, imageIndex = primaryImageIndex(node)) {
  const urls = imageResultUrls(node);
  return urls[normalizedImageIndex(node, imageIndex)] || '';
}

function isExpandedImageNode(node: CanvasNodeModel) {
  return node.type === 'image' && Boolean(node.galleryExpanded) && imageResultUrls(node).length > 1;
}

function baseMediaNodeWidth(node: CanvasNodeModel | NodeKind) {
  const kind = typeof node === 'string' ? node : node.type;
  return kind === 'image' ? IMAGE_NODE_WIDTH : VIDEO_NODE_WIDTH;
}

function mediaNodeWidth(node: CanvasNodeModel | NodeKind) {
  if (typeof node === 'string' || !isExpandedImageNode(node)) return baseMediaNodeWidth(node);
  const count = imageResultUrls(node).length;
  return Math.min(
    MEDIA_NODE_EXPANDED_MAX_WIDTH,
    Math.max(IMAGE_NODE_WIDTH, count * MEDIA_NODE_EXPANDED_CARD_WIDTH + (count - 1) * MEDIA_NODE_EXPANDED_GAP),
  );
}

function hasMediaPreview(node: CanvasNodeModel) {
  if (node.resultUrl || node.resultUrls?.some(Boolean)) return true;
  if (node.type === 'video') return Boolean(node.inputUrl);
  return node.references.some((reference) => reference.uploadState !== 'failed');
}

function mediaStageHeight(node: CanvasNodeModel | NodeKind) {
  const kind = typeof node === 'string' ? node : node.type;
  const defaultHeight = kind === 'image'
    ? IMAGE_NODE_DEFAULT_STAGE_HEIGHT
    : VIDEO_NODE_DEFAULT_STAGE_HEIGHT;
  if (typeof node === 'string' || !hasMediaPreview(node)) return defaultHeight;
  const ratio = node.previewAspectRatio;
  if (!ratio || !Number.isFinite(ratio) || ratio <= 0) return defaultHeight;
  const naturalHeight = baseMediaNodeWidth(node) / ratio;
  return kind === 'image'
    ? Math.min(IMAGE_NODE_MAX_STAGE_HEIGHT, naturalHeight)
    : naturalHeight;
}

function mediaNodeHeight(node: CanvasNodeModel | NodeKind, showPrompt = true) {
  return MEDIA_NODE_HEADER_HEIGHT
    + mediaStageHeight(node)
    + (showPrompt ? MEDIA_NODE_PROMPT_GAP + MEDIA_NODE_PROMPT_HEIGHT : 0);
}

function nodeDimensions(node: CanvasNodeModel | NodeKind) {
  const kind = typeof node === 'string' ? node : node.type;
  return kind === 'text'
    ? { width: TEXT_NODE_WIDTH, height: TEXT_NODE_HEIGHT }
    : {
      width: mediaNodeWidth(node),
      height: mediaNodeHeight(node, typeof node === 'string' || selectedId.value === node.id),
    };
}

function layoutNodeDimensions(node: CanvasNodeModel | NodeKind) {
  const kind = typeof node === 'string' ? node : node.type;
  return kind === 'text'
    ? { width: TEXT_NODE_WIDTH, height: TEXT_NODE_HEIGHT }
    : { width: mediaNodeWidth(node), height: mediaNodeHeight(node) };
}

function nodeConnectionOffset(node: CanvasNodeModel) {
  return node.type === 'text'
    ? TEXT_NODE_HEIGHT / 2
    : MEDIA_NODE_HEADER_HEIGHT + mediaStageHeight(node) / 2;
}

function nodeOutputConnectionPoint(node: CanvasNodeModel, requestedImageIndex?: number) {
  const y = node.y + nodeConnectionOffset(node);
  if (!isExpandedImageNode(node)) {
    return { x: node.x + nodeDimensions(node).width, y };
  }
  const count = imageResultUrls(node).length;
  const index = normalizedImageIndex(node, requestedImageIndex);
  const width = mediaNodeWidth(node);
  const cardWidth = (width - (count - 1) * MEDIA_NODE_EXPANDED_GAP) / count;
  return {
    x: node.x + (index + 1) * cardWidth + index * MEDIA_NODE_EXPANDED_GAP,
    y,
  };
}

const nodeById = computed(() => new Map(nodes.value.map((node) => [node.id, node])));
const incomingConnectionsByNode = computed(() => {
  const index = new Map<string, CanvasConnection[]>();
  for (const connection of connections.value) {
    const entries = index.get(connection.to);
    if (entries) entries.push(connection);
    else index.set(connection.to, [connection]);
  }
  return index;
});
const outgoingConnectionsByNode = computed(() => {
  const index = new Map<string, CanvasConnection[]>();
  for (const connection of connections.value) {
    const entries = index.get(connection.from);
    if (entries) entries.push(connection);
    else index.set(connection.from, [connection]);
  }
  return index;
});
const activeOperationNodeIds = computed(() => nodes.value
  .filter((node) => node.status === 'generating' || node.status === 'uploading')
  .map((node) => node.id));

const {
  surfaceWidth,
  surfaceHeight,
  visibleNodeIds,
  visibleNodes,
  fitNodes,
  locateNode,
  navigateToWorld,
} = useCanvasViewport({
  canvas,
  nodes,
  viewport,
  nodeRevision: canvasDocumentRevision,
  getNodeSize: layoutNodeDimensions,
  keepNodeIds: () => [
    ...selectedIds.value,
    ...activeOperationNodeIds.value,
    ...(nodeDrag.value ? [nodeDrag.value.id] : []),
    ...(connectionSourceId.value ? [connectionSourceId.value] : []),
    ...(connectionTargetId.value ? [connectionTargetId.value] : []),
  ],
  minZoom: MIN_ZOOM,
  maxZoom: MAX_ZOOM,
  onViewportChange: scheduleViewportDirty,
});

const visibleConnections = computed(() => {
  const visible = new Map<string, CanvasConnection>();
  for (const nodeId of visibleNodeIds.value) {
    for (const connection of incomingConnectionsByNode.value.get(nodeId) || []) visible.set(connection.id, connection);
    for (const connection of outgoingConnectionsByNode.value.get(nodeId) || []) visible.set(connection.id, connection);
  }
  return [...visible.values()];
});

const minimapNodes = computed(() => {
  canvasDocumentRevision.value;
  return (toRaw(nodes.value) as CanvasNodeModel[]).map((node) => ({
    id: node.id,
    type: node.type,
    x: node.x,
    y: node.y,
    ...layoutNodeDimensions(node),
  }));
});

const selectionBoxStyle = computed(() => {
  const box = selectionBox.value;
  if (!box) return {};
  return {
    left: `${Math.min(box.startX, box.currentX)}px`,
    top: `${Math.min(box.startY, box.currentY)}px`,
    width: `${Math.abs(box.currentX - box.startX)}px`,
    height: `${Math.abs(box.currentY - box.startY)}px`,
  };
});
const selectedIdList = computed(() => [...selectedIds.value]);
const canvasPromptWidth = computed(() => Math.min(
  MEDIA_NODE_PROMPT_WIDTH,
  Math.max(320, surfaceWidth.value - 24),
));

const worldStyle = computed(() => ({
  transform: `translate(${viewport.value.x}px, ${viewport.value.y}px) scale(${viewport.value.zoom})`,
}));
const canvasStyle = computed(() => {
  const gridSize = GRID_SIZE * viewport.value.zoom;
  return {
    '--canvas-grid-size': `${gridSize}px`,
    '--canvas-grid-x': `${viewport.value.x % gridSize}px`,
    '--canvas-grid-y': `${viewport.value.y % gridSize}px`,
  };
});
const connectionLines = computed<ConnectionLine[]>(() => visibleConnections.value.flatMap((connection) => {
  const from = nodeById.value.get(connection.from);
  const to = nodeById.value.get(connection.to);
  if (!from || !to) return [];
  const source = nodeOutputConnectionPoint(from, connection.sourceImageIndex);
  return [{
    id: connection.id,
    x1: viewport.value.x + source.x * viewport.value.zoom,
    y1: viewport.value.y + source.y * viewport.value.zoom,
    x2: viewport.value.x + to.x * viewport.value.zoom,
    y2: viewport.value.y + (to.y + nodeConnectionOffset(to)) * viewport.value.zoom,
  }];
}));
const previewConnectionLine = computed<ConnectionLine | undefined>(() => {
  const preview = connectionPreview.value;
  const source = preview && nodeById.value.get(preview.sourceId);
  if (!preview || !source) return undefined;
  const sourcePoint = nodeOutputConnectionPoint(source, connectionSourceImageIndex.value);
  const target = connectionTargetId.value
    ? nodeById.value.get(connectionTargetId.value)
    : undefined;
  const targetOffset = target
    ? connectionTargetPoint.value?.nodeId === target.id
      ? connectionTargetPoint.value.offset
      : nodeConnectionOffset(target)
    : undefined;
  const endpoint = target
    ? { x: target.x, y: target.y + (targetOffset ?? nodeConnectionOffset(target)) }
    : { x: preview.x, y: preview.y };
  return {
    id: 'connection-preview',
    x1: viewport.value.x + sourcePoint.x * viewport.value.zoom,
    y1: viewport.value.y + sourcePoint.y * viewport.value.zoom,
    x2: viewport.value.x + endpoint.x * viewport.value.zoom,
    y2: viewport.value.y + endpoint.y * viewport.value.zoom,
  };
});
const connectionPreviewPortStyle = computed(() => {
  const line = previewConnectionLine.value;
  if (!line || connectionTargetId.value) return {};
  return {
    left: `${line.x2}px`,
    top: `${line.y2}px`,
  };
});
const contextMenuStyle = computed(() => {
  const menu = contextMenu.value;
  if (!menu) return {};
  const bounds = canvas.value?.getBoundingClientRect();
  const menuWidth = 190;
  const menuHeight = menu.connectionId ? 92 : menu.nodeId ? 150 : 184;
  const width = bounds?.width || menu.x + menuWidth + 8;
  const height = bounds?.height || menu.y + menuHeight + 8;
  return {
    left: `${Math.max(8, Math.min(menu.x, width - menuWidth - 8))}px`,
    top: `${Math.max(8, Math.min(menu.y, height - menuHeight - 8))}px`,
  };
});
const connectionDeleteControlStyle = computed(() => ({
  left: `${connectionDeleteControl.value?.x || 0}px`,
  top: `${connectionDeleteControl.value?.y || 0}px`,
}));
const connectionHint = computed(() => {
  const source = connectionSourceId.value ? nodeById.value.get(connectionSourceId.value) : undefined;
  if (source?.type === 'image' && imageResultUrls(source).length > 1) {
    const index = connectionSourceImageIndex.value ?? primaryImageIndex(source);
    return `使用图 ${index + 1} 连接至目标节点`;
  }
  return '连接至目标节点';
});

function makeId(prefix: string) {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
    : Math.random().toString(36).slice(2, 12);
  return `${prefix}_${Date.now().toString(36)}_${random}`;
}

function nodeTitle(kind: NodeKind) {
  const prefix = kind === 'image' ? '图片节点' : kind === 'video' ? '视频节点' : '文本节点';
  const used = new Set(nodes.value
    .filter((node) => node.type === kind)
    .map((node) => Number(node.title.match(/(\d+)\s*$/)?.[1] || 0))
    .filter((value) => value > 0));
  let index = 1;
  while (used.has(index)) index += 1;
  return `${prefix} ${index}`;
}

function createNode(kind: NodeKind, x?: number, y?: number): CanvasNodeModel {
  const offset = nodes.value.length;
  const dimensions = nodeDimensions(kind);
  return {
    id: makeId(kind),
    type: kind,
    title: nodeTitle(kind),
    x: x ?? 220 + (offset % 3) * (dimensions.width + 44),
    y: y ?? 170 + Math.floor(offset / 3) * (dimensions.height + 44),
    prompt: '',
    content: '',
    aspectRatio: '1:1',
    imageSize: '1K',
    quality: 'auto',
    thinkingLevel: 'minimal',
    duration: 5,
    imageCount: 1,
    primaryImageIndex: 0,
    galleryExpanded: false,
    references: [],
    model: kind === 'image' ? modelOptions.value.imageModel : undefined,
    status: 'idle',
  };
}

function nodePlacement(kind: NodeKind = 'image') {
  const bounds = canvas.value?.getBoundingClientRect();
  if (!bounds) return undefined;

  const dimensions = nodeDimensions(kind);

  const centerX = (bounds.width / 2 - viewport.value.x) / viewport.value.zoom;
  const centerY = (bounds.height / 2 - viewport.value.y) / viewport.value.zoom;
  const originX = centerX - dimensions.width / 2;
  const originY = centerY - dimensions.height / 2;
  const stepX = dimensions.width + 44;
  const stepY = dimensions.height + 44;

  const isAvailable = (candidate: { x: number; y: number }) => !nodes.value.some((node) => {
    const nodeSize = layoutNodeDimensions(node);
    return candidate.x < node.x + nodeSize.width + 20
      && candidate.x + dimensions.width + 20 > node.x
      && candidate.y < node.y + nodeSize.height + 20
      && candidate.y + dimensions.height + 20 > node.y;
  });

  for (let radius = 0; radius <= 12; radius += 1) {
    const offsets = radius === 0
      ? [[0, 0]]
      : [
        [radius, 0],
        [0, radius],
        [-radius, 0],
        [0, -radius],
        [radius, radius],
        [-radius, radius],
        [radius, -radius],
        [-radius, -radius],
      ];
    for (const [column, row] of offsets) {
      const candidate = { x: originX + column * stepX, y: originY + row * stepY };
      if (isAvailable(candidate)) return candidate;
    }
  }

  return { x: originX + nodes.value.length * 28, y: originY + nodes.value.length * 28 };
}

function fitRecentNodesInViewport() {
  void nextTick(() => window.requestAnimationFrame(() => {
    const bounds = canvas.value?.getBoundingClientRect();
    if (!bounds || !nodes.value.length) return;
    const padding = 56;
    const boxes = nodes.value.map((node) => {
      const dimensions = layoutNodeDimensions(node);
      return {
        left: node.x,
        top: node.y,
        right: node.x + dimensions.width,
        bottom: node.y + dimensions.height,
      };
    }).filter((box) => Object.values(box).every((value) => Number.isFinite(value)));
    if (!boxes.length) return;
    const left = Math.min(...boxes.map((box) => box.left));
    const top = Math.min(...boxes.map((box) => box.top));
    const right = Math.max(...boxes.map((box) => box.right));
    const bottom = Math.max(...boxes.map((box) => box.bottom));
    const contentWidth = Math.max(1, right - left);
    const contentHeight = Math.max(1, bottom - top);
    const availableWidth = Math.max(160, bounds.width - padding);
    const availableHeight = Math.max(160, bounds.height - padding);
    const hasFixedPrompt = nodes.value.some((node) => (
      node.id === selectedId.value && (node.type === 'image' || node.type === 'video')
    ));
    const fixedPromptHeight = hasFixedPrompt ? MEDIA_NODE_PROMPT_HEIGHT : 0;
    const fixedPromptZoom = fixedPromptHeight > 0 && contentHeight > fixedPromptHeight
      ? (availableHeight - fixedPromptHeight) / (contentHeight - fixedPromptHeight)
      : availableHeight / contentHeight;
    const candidates = [
      viewport.value.zoom,
      availableWidth / contentWidth,
      availableHeight / contentHeight,
      fixedPromptZoom,
    ].filter((value) => Number.isFinite(value) && value > 0);
    if (!candidates.length) return;
    const zoom = Math.max(MIN_ZOOM, Math.min(...candidates));
    viewport.value = {
      zoom,
      x: bounds.width / 2 - (left + contentWidth / 2) * zoom,
      y: bounds.height / 2 - (top + contentHeight / 2) * zoom,
    };
    scheduleViewportDirty();
  }));
}

function fitAllNodes() {
  if (!nodes.value.length) {
    resetViewport();
    return;
  }
  void fitNodes();
}

function locateSelectedNode() {
  void locateNode(selectedId.value);
}

function showToast(message: string) {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = '';
  }, 4200);
}

function messageFrom(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。';
}

function mergeVideoAnalysisAsset(node: CanvasNodeModel, asset: AgentVideoAsset) {
  if (node.type !== 'video' || node.inputVideoId !== asset.videoId) return;
  const status = normalizeVideoAnalysisStatus(asset.analysisStatus || asset.status);
  const analysis = normalizeCanvasVideoAnalysis(asset.analysis);
  node.videoAnalysisStatus = status;
  node.videoAnalysisError = asset.analysisError
    || (status === 'failed' ? '视频解析失败，请重试。' : undefined);
  if (analysis) node.videoAnalysis = analysis;
  if (asset.url && !node.inputUrl) node.inputUrl = asset.url;
}

function activeVideoAnalysisIds() {
  return nodes.value
    .filter((node) => node.type === 'video'
      && Boolean(node.inputVideoId)
      && ACTIVE_VIDEO_ANALYSIS_STATUSES.has(normalizeVideoAnalysisStatus(node.videoAnalysisStatus)))
    .map((node) => node.inputVideoId as string);
}

function stopVideoAnalysisPolling() {
  videoAnalysisPollEpoch += 1;
  if (videoAnalysisPollTimer !== undefined) {
    window.clearTimeout(videoAnalysisPollTimer);
    videoAnalysisPollTimer = undefined;
  }
}

function scheduleVideoAnalysisPolling(delay = VIDEO_ANALYSIS_POLL_INTERVAL_MS) {
  if (videoAnalysisPollTimer !== undefined) window.clearTimeout(videoAnalysisPollTimer);
  videoAnalysisPollTimer = undefined;
  if (!activeVideoAnalysisIds().length) return;
  videoAnalysisPollTimer = window.setTimeout(() => {
    videoAnalysisPollTimer = undefined;
    void pollVideoAnalysisStatuses();
  }, Math.max(250, delay));
}

async function pollVideoAnalysisStatuses(extraIds: string[] = []) {
  if (videoAnalysisPollInFlight) {
    scheduleVideoAnalysisPolling(500);
    return;
  }
  const ids = Array.from(new Set([...extraIds, ...activeVideoAnalysisIds()].filter(Boolean))).slice(0, 50);
  if (!ids.length) return;

  const requestEpoch = videoAnalysisPollEpoch;
  videoAnalysisPollInFlight = true;
  try {
    const response = await fetchCanvasVideoAnalysisStatuses(ids);
    if (requestEpoch !== videoAnalysisPollEpoch) return;
    const updates = new Map(response.items.map((asset) => [asset.videoId, asset]));
    const missing = new Set(response.missing || []);
    let changed = false;
    for (const node of nodes.value) {
      if (node.type !== 'video' || !node.inputVideoId) continue;
      const update = updates.get(node.inputVideoId);
      if (update) {
        const previous = JSON.stringify([
          node.videoAnalysisStatus,
          node.videoAnalysisError,
          node.videoAnalysis,
          node.inputUrl,
        ]);
        mergeVideoAnalysisAsset(node, update);
        changed ||= previous !== JSON.stringify([
          node.videoAnalysisStatus,
          node.videoAnalysisError,
          node.videoAnalysis,
          node.inputUrl,
        ]);
      }
      else if (missing.has(node.inputVideoId)) {
        node.videoAnalysisStatus = 'failed';
        node.videoAnalysisError = '视频附件不存在或无权访问。';
        changed = true;
      }
    }
    if (changed) touchCanvasDocument();
  } catch {
    // Parsing continues on the server; a transient status request must not discard the uploaded video.
  } finally {
    videoAnalysisPollInFlight = false;
    if (requestEpoch === videoAnalysisPollEpoch) scheduleVideoAnalysisPolling();
  }
}

async function retryVideoAnalysis(nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node?.inputVideoId || retryingVideoAnalysisIds.value.has(node.inputVideoId)) return;
  const videoId = node.inputVideoId;
  retryingVideoAnalysisIds.value = new Set([...retryingVideoAnalysisIds.value, videoId]);
  try {
    const asset = await retryCanvasVideoAnalysis(videoId);
    mergeVideoAnalysisAsset(node, asset);
    touchCanvasDocument();
    scheduleVideoAnalysisPolling(250);
    showToast('视频已重新加入解析队列。');
  } catch (error) {
    node.videoAnalysisStatus = 'failed';
    node.videoAnalysisError = messageFrom(error);
    showToast(`重试视频解析失败：${messageFrom(error)}`);
  } finally {
    const next = new Set(retryingVideoAnalysisIds.value);
    next.delete(videoId);
    retryingVideoAnalysisIds.value = next;
  }
}

function closeContextMenu() {
  contextMenu.value = undefined;
}

function addNode(kind: NodeKind, x?: number, y?: number) {
  const placedFromContext = x !== undefined && y !== undefined;
  const placement = x !== undefined && y !== undefined ? { x, y } : nodePlacement(kind);
  const node = createNode(kind, placement?.x, placement?.y);
  nodes.value.push(node);
  selectOnly(node.id);
  clearConnectionState();
  closeContextMenu();
  commitCanvasHistory();
    // Keep a newly added node fully actionable even when it was placed near an edge.
    // The viewport only changes when the node would otherwise be clipped.
  if (!placedFromContext) {
    fitRecentNodesInViewport();
  } else {
    void nextTick(() => {
      const bounds = canvas.value?.getBoundingClientRect();
      const nodeElement = document.querySelector<HTMLElement>(`[data-canvas-node-id="${node.id}"]`);
      const nodeBounds = nodeElement?.getBoundingClientRect();
      const promptBounds = nodeElement?.querySelector<HTMLElement>('.media-prompt-editor')?.getBoundingClientRect();
      if (!bounds || !nodeBounds) return;
      const visibleBounds = promptBounds
        ? {
            top: Math.min(nodeBounds.top, promptBounds.top),
            right: Math.max(nodeBounds.right, promptBounds.right),
            bottom: Math.max(nodeBounds.bottom, promptBounds.bottom),
            left: Math.min(nodeBounds.left, promptBounds.left),
          }
        : nodeBounds;
      const margin = 32;
      const clipped = visibleBounds.top < bounds.top + margin
        || visibleBounds.bottom > bounds.bottom - margin
        || visibleBounds.left < bounds.left + margin
        || visibleBounds.right > bounds.right - margin;
      if (!clipped) return;

      const width = visibleBounds.right - visibleBounds.left;
      const height = visibleBounds.bottom - visibleBounds.top;
      const shiftX = visibleBounds.left < bounds.left + margin
        ? bounds.left + margin - visibleBounds.left
        : visibleBounds.right > bounds.right - margin
          ? bounds.right - margin - visibleBounds.right
          : 0;
      const shiftY = visibleBounds.top < bounds.top + margin
        ? bounds.top + margin - visibleBounds.top
        : visibleBounds.bottom > bounds.bottom - margin
          ? bounds.bottom - margin - visibleBounds.bottom
          : 0;
      if (!Number.isFinite(width) || !Number.isFinite(height) || !Number.isFinite(shiftX) || !Number.isFinite(shiftY)) return;
      viewport.value = {
        ...viewport.value,
        x: viewport.value.x + shiftX,
        y: viewport.value.y + shiftY,
      };
    });
  }
}

function addNodeFromContext(kind: NodeKind) {
  const menu = contextMenu.value;
  const dimensions = nodeDimensions(kind);
  const x = Number.isFinite(menu?.worldX) ? (menu!.worldX as number) - dimensions.width / 2 : undefined;
  const y = Number.isFinite(menu?.worldY) ? (menu!.worldY as number) - dimensions.height / 2 : undefined;
  addNode(kind, x, y);
}

function duplicateNode(nodeId: string) {
  const source = nodeById.value.get(nodeId);
  if (!source) return;
  const copy: CanvasNodeModel = {
    ...source,
    id: makeId(source.type),
    title: nodeTitle(source.type),
    x: source.x + 32,
    y: source.y + 32,
    references: source.references.map((reference) => ({
      ...reference,
      id: makeId('ref'),
    })),
    galleryExpanded: false,
    status: source.resultUrl || source.resultUrls?.some(Boolean) ? 'ready' : 'idle',
    error: undefined,
  };
  nodes.value.push(copy);
  selectOnly(copy.id);
  clearConnectionState();
  closeContextMenu();
  commitCanvasHistory();
}

function copySelectedNodes() {
  if (!selectedIds.value.size) return false;
  const copiedIds = new Set(selectedIds.value);
  const copiedNodes = nodes.value
    .filter((node) => copiedIds.has(node.id))
    .map((node) => JSON.parse(JSON.stringify(node)) as CanvasNodeModel);
  if (!copiedNodes.length) return false;
  canvasClipboard.value = {
    nodes: copiedNodes,
    connections: connections.value
      .filter((connection) => copiedIds.has(connection.from) && copiedIds.has(connection.to))
      .map((connection) => ({ ...connection })),
    pasteCount: 0,
  };
  showToast(`已复制 ${copiedNodes.length} 个节点。`);
  return true;
}

function pasteCanvasClipboard() {
  const clipboard = canvasClipboard.value;
  if (!clipboard?.nodes.length) return;
  clipboard.pasteCount += 1;
  const offset = 36 * clipboard.pasteCount;
  const idMap = new Map<string, string>();
  const pastedIds: string[] = [];
  for (const source of clipboard.nodes) {
    const copy = JSON.parse(JSON.stringify(source)) as CanvasNodeModel;
    const nextId = makeId(source.type);
    idMap.set(source.id, nextId);
    copy.id = nextId;
    copy.title = nodeTitle(source.type);
    copy.x = source.x + offset;
    copy.y = source.y + offset;
    copy.references = source.references.map((reference) => ({ ...reference, id: makeId('ref') }));
    copy.galleryExpanded = false;
    copy.error = undefined;
    if (source.status === 'generating' || source.status === 'uploading') {
      copy.status = source.resultUrl || source.resultUrls?.some(Boolean) ? 'ready' : 'idle';
      copy.taskId = undefined;
      copy.progress = undefined;
    }
    nodes.value.push(copy);
    pastedIds.push(nextId);
  }
  for (const connection of clipboard.connections) {
    const from = idMap.get(connection.from);
    const to = idMap.get(connection.to);
    if (from && to) connections.value.push({ ...connection, id: makeId('edge'), from, to });
  }
  selectMany(pastedIds, { primaryId: pastedIds.at(-1) });
  clearConnectionState();
  closeContextMenu();
  commitCanvasHistory();
  showToast(`已粘贴 ${pastedIds.length} 个节点。`);
}

async function removeNodeIds(nodeIds: Iterable<string>) {
  const requested = [...new Set(nodeIds)];
  const targets = requested
    .map((nodeId) => nodeById.value.get(nodeId))
    .filter((node): node is CanvasNodeModel => Boolean(node && !deletingNodeIds.has(node.id)));
  if (!targets.length) return;
  const active = targets.filter((node) => node.status === 'generating' || node.status === 'uploading');
  if (active.length) {
    const label = targets.length === 1 ? `“${targets[0].title}”仍在运行` : `选中的节点中有 ${active.length} 个任务仍在运行`;
    if (!window.confirm(`${label}。确定取消任务并删除吗？`)) return;
  }

  const removableIds = new Set(targets.filter((node) => !active.includes(node)).map((node) => node.id));
  let cancellationPending = false;
  await Promise.all(active.map(async (node) => {
    deletingNodeIds.add(node.id);
    try {
      if (node.status === 'generating' && node.taskId) {
        if (node.type === 'video') {
          const canceled = await cancelVideoGenerationTask(node.taskId);
          cancellationPending ||= Boolean(canceled.cancellation_pending);
        } else {
          await cancelImageTask(node.taskId);
        }
      }
      taskControllers.get(node.id)?.abort();
      removableIds.add(node.id);
    } catch (error) {
      showToast(`取消任务失败，节点已保留：${node.title}：${messageFrom(error)}`);
    } finally {
      deletingNodeIds.delete(node.id);
    }
  }));
  if (!removableIds.size) return;

  nodes.value = nodes.value.filter((node) => !removableIds.has(node.id));
  connections.value = connections.value.filter((connection) => (
    !removableIds.has(connection.from) && !removableIds.has(connection.to)
  ));
  removeFromSelection(removableIds);
  if (connectionSourceId.value && removableIds.has(connectionSourceId.value)) clearConnectionState();
  if (connectionDeleteControl.value && !connections.value.some((connection) => connection.id === connectionDeleteControl.value?.id)) {
    clearConnectionDeleteControl();
  }
  scheduleVideoAnalysisPolling();
  closeContextMenu();
  if (active.length) {
    replaceCanvasHistoryCurrent();
    showToast(cancellationPending
      ? '节点已删除，部分上游视频可能仍会产生费用，系统会继续核对任务状态。'
      : `已取消任务并删除 ${removableIds.size} 个节点。`);
  } else {
    commitCanvasHistory();
  }
}

async function removeNode(nodeId: string) {
  await removeNodeIds([nodeId]);
}

async function removeSelectedNodes() {
  await removeNodeIds(selectedIds.value);
}

function removeConnection(connectionId: string) {
  connections.value = connections.value.filter((connection) => connection.id !== connectionId);
  if (connectionDeleteControl.value?.id === connectionId || pendingConnectionHover?.id === connectionId) {
    clearConnectionDeleteControl();
  }
  closeContextMenu();
  commitCanvasHistory();
}

function connectionCreatesCycle(from: string, to: string) {
  const pending = [to];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop();
    if (!current || visited.has(current)) continue;
    if (current === from) return true;
    visited.add(current);
    pending.push(...(outgoingConnectionsByNode.value.get(current) || []).map((connection) => connection.to));
  }
  return false;
}

function connectNodes(from: string, to: string, requestedImageIndex?: number) {
  if (from === to) return;
  const source = nodeById.value.get(from);
  const sourceImageIndex = source?.type === 'image'
    ? normalizedImageIndex(source, requestedImageIndex)
    : undefined;
  const existing = connections.value.find((connection) => connection.from === from && connection.to === to);
  if (existing) {
    if (sourceImageIndex !== undefined && existing.sourceImageIndex !== sourceImageIndex) {
      existing.sourceImageIndex = sourceImageIndex;
      commitCanvasHistory();
    }
    return;
  }
  if (connectionCreatesCycle(from, to)) {
    showToast('节点不能形成循环连接。');
    return;
  }
  connections.value.push({ id: makeId('edge'), from, to, sourceImageIndex });
  commitCanvasHistory();
}

function mediaReferenceLabel(node: CanvasNodeModel) {
  const match = node.title.match(/^(图片|视频)节点\s*(.+)$/);
  return match ? `${match[1]} ${match[2]}` : node.title;
}

function mediaReferencePreview(node: CanvasNodeModel, imageIndex?: number) {
  if (node.type === 'video') return node.resultUrl || node.inputUrl || '';
  return imageResultUrl(node, imageIndex ?? primaryImageIndex(node))
    || [...node.references].reverse().find((reference) => reference.uploadState !== 'failed')?.previewUrl
    || '';
}

function canvasMediaReference(node: CanvasNodeModel, direct: boolean, requestedImageIndex?: number): CanvasMediaReference {
  const resultCount = node.type === 'image' ? imageResultUrls(node).length : 0;
  const imageIndex = node.type === 'image' && resultCount
    ? normalizedImageIndex(node, requestedImageIndex)
    : undefined;
  const resultLabel = imageIndex !== undefined && resultCount > 1 ? ` · 图 ${imageIndex + 1}` : '';
  return {
    nodeId: node.id,
    type: node.type as 'image' | 'video',
    label: `${mediaReferenceLabel(node)}${resultLabel}`,
    title: `${node.title}${resultLabel}`,
    previewUrl: mediaReferencePreview(node, imageIndex),
    imageIndex,
    direct,
  };
}

function promptReferences(nodeId: string) {
  return directSourceNodes(nodeId)
    .filter((node): node is CanvasNodeModel & { type: 'image' | 'video' } => node.type === 'image' || node.type === 'video')
    .map((node) => canvasMediaReference(node, true, sourceImageIndexForTarget(node, nodeId)));
}

function promptReferenceCandidates(nodeId: string) {
  const referencedIds = new Set((incomingConnectionsByNode.value.get(nodeId) || [])
    .map((connection) => connection.from));
  return nodes.value
    .filter((node) => node.id !== nodeId && (node.type === 'image' || node.type === 'video'))
    .filter((node) => !referencedIds.has(node.id) && !connectionCreatesCycle(node.id, nodeId))
    .map((node) => canvasMediaReference(node, true, node.type === 'image' ? primaryImageIndex(node) : undefined));
}

function addPromptReference(targetId: string, sourceId: string) {
  const target = nodeById.value.get(targetId);
  const source = nodeById.value.get(sourceId);
  if (!target || !source || source.type === 'text') return;
  connectNodes(source.id, target.id, source.type === 'image' ? primaryImageIndex(source) : undefined);
  selectOnly(target.id);
}

function removePromptReference(targetId: string, sourceId: string) {
  const connection = connections.value.find((item) => item.from === sourceId && item.to === targetId);
  if (!connection) return;
  removeConnection(connection.id);
  selectOnly(targetId);
}

function duplicateContextNode() {
  const nodeId = contextMenu.value?.nodeId;
  if (nodeId) duplicateNode(nodeId);
}

function removeContextNode() {
  const nodeId = contextMenu.value?.nodeId;
  if (nodeId) removeNode(nodeId);
}

function removeContextConnection() {
  const connectionId = contextMenu.value?.connectionId;
  if (connectionId) removeConnection(connectionId);
}

function updateCanvasNode(
  nodeId: string,
  payload: { key: keyof CanvasNodeModel; value: CanvasNodeModel[keyof CanvasNodeModel] },
) {
  const node = nodeById.value.get(nodeId);
  if (!node) return;
  Object.assign(node, { [payload.key]: payload.value });
  scheduleCanvasHistoryCommit();
}

function setPrimaryImage(nodeId: string, imageIndex: number) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type !== 'image') return;
  const urls = imageResultUrls(node);
  if (!urls.length) return;
  const index = normalizedImageIndex(node, Math.round(imageIndex));
  node.primaryImageIndex = index;
  node.resultUrl = urls[index];
  commitCanvasHistory();
}

function toggleImageGallery(nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type !== 'image' || imageResultUrls(node).length < 2) return;
  node.galleryExpanded = !node.galleryExpanded;
}

function selectNode(nodeId: string) {
  selectOnly(nodeId);
}

const IMAGE_COUNT_WORDS: Record<string, number> = {
  '一': 1,
  '二': 2,
  '三': 3,
  '四': 4,
};

function imageCountFromPrompt(value: string) {
  const text = value.trim();
  const match = text.match(/(?:生成|产出|制作|创建|输出|给我|generate|create|make|produce)?\s*(\d+|[一二三四])\s*(?:张|幅|个)?\s*(?:图片|图像|图|images?|pictures?)/iu);
  if (!match) return undefined;
  const parsed = /^\d+$/.test(match[1]) ? Number(match[1]) : IMAGE_COUNT_WORDS[match[1]];
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 4 ? parsed : undefined;
}

function updateNodePrompt(nodeId: string, value: string) {
  const node = nodeById.value.get(nodeId);
  if (!node) return;
  node.prompt = value;
  if (node.type === 'image') {
    const detectedCount = imageCountFromPrompt(value);
    if (detectedCount !== undefined) node.imageCount = detectedCount;
  }
  if (node.error) node.error = undefined;
  scheduleCanvasHistoryCommit();
}

function startNodeInteraction(event: PointerEvent, nodeId: string) {
  if (event.button !== 0) return;
  const node = nodeById.value.get(nodeId);
  if (!node) return;
  event.preventDefault();
  closeContextMenu();

  if (connectionSourceId.value) {
    if (connectionSourceId.value !== nodeId) {
      connectNodes(connectionSourceId.value, nodeId, connectionSourceImageIndex.value);
    }
    clearConnectionState();
    selectOnly(nodeId);
    return;
  }

  const additive = event.shiftKey || event.ctrlKey || event.metaKey;
  if (additive) {
    toggleSelection(nodeId);
    if (!isSelected(nodeId)) return;
  } else if (!isSelected(nodeId)) {
    selectOnly(nodeId);
  } else {
    selectMany(selectedIds.value, { primaryId: nodeId });
  }
  const origins = new Map<string, { x: number; y: number }>();
  for (const selectedNodeId of selectedIds.value) {
    const selectedNode = nodeById.value.get(selectedNodeId);
    if (selectedNode) origins.set(selectedNodeId, { x: selectedNode.x, y: selectedNode.y });
  }
  nodeDrag.value = {
    id: nodeId,
    clientX: event.clientX,
    clientY: event.clientY,
    origins,
  };
}

function pointInWorld(clientX: number, clientY: number) {
  const bounds = canvas.value?.getBoundingClientRect();
  if (!bounds) return undefined;
  return {
    x: (clientX - bounds.left - viewport.value.x) / viewport.value.zoom,
    y: (clientY - bounds.top - viewport.value.y) / viewport.value.zoom,
  };
}

function connectionPath(line: ConnectionLine) {
  const direction = line.x2 >= line.x1 ? 1 : -1;
  const horizontalDistance = Math.abs(line.x2 - line.x1);
  const verticalDistance = Math.abs(line.y2 - line.y1);
  const bend = Math.max(42, Math.min(240, horizontalDistance * 0.42 + verticalDistance * 0.08));
  const lift = Math.min(56, verticalDistance * 0.16);
  return [
    `M ${line.x1} ${line.y1}`,
    `C ${line.x1 + direction * bend} ${line.y1 - lift},`,
    `${line.x2 - direction * bend} ${line.y2 + lift},`,
    `${line.x2} ${line.y2}`,
  ].join(' ');
}

function clearConnectionHoverTimer() {
  if (connectionHoverTimer === undefined) return;
  window.clearTimeout(connectionHoverTimer);
  connectionHoverTimer = undefined;
}

function clearConnectionHideTimer() {
  if (connectionHideTimer === undefined) return;
  window.clearTimeout(connectionHideTimer);
  connectionHideTimer = undefined;
}

function clearConnectionDeleteControl() {
  clearConnectionHoverTimer();
  clearConnectionHideTimer();
  pendingConnectionHover = undefined;
  connectionDeleteControl.value = undefined;
}

function connectionHoverPoint(event: PointerEvent) {
  const bounds = canvas.value?.getBoundingClientRect();
  if (!bounds) return undefined;
  const inset = 26;
  return {
    x: Math.max(inset, Math.min(event.clientX - bounds.left, bounds.width - inset)),
    y: Math.max(inset, Math.min(event.clientY - bounds.top, bounds.height - inset)),
  };
}

function enterConnectionHover(event: PointerEvent, connectionId: string) {
  if (event.pointerType === 'touch') return;
  clearConnectionHideTimer();
  if (connectionDeleteControl.value?.id === connectionId) return;

  clearConnectionHoverTimer();
  connectionDeleteControl.value = undefined;
  const point = connectionHoverPoint(event);
  if (!point) return;
  pendingConnectionHover = { id: connectionId, ...point };
  connectionHoverTimer = window.setTimeout(() => {
    connectionHoverTimer = undefined;
    const pending = pendingConnectionHover;
    if (!pending || !connections.value.some((connection) => connection.id === pending.id)) return;
    connectionDeleteControl.value = pending;
  }, CONNECTION_DELETE_HOVER_DELAY_MS);
}

function moveConnectionHover(event: PointerEvent, connectionId: string) {
  if (connectionDeleteControl.value?.id === connectionId || pendingConnectionHover?.id !== connectionId) return;
  const point = connectionHoverPoint(event);
  if (point) pendingConnectionHover = { id: connectionId, ...point };
}

function leaveConnectionHover(connectionId: string) {
  if (pendingConnectionHover?.id === connectionId) pendingConnectionHover = undefined;
  clearConnectionHoverTimer();
  if (connectionDeleteControl.value?.id !== connectionId) return;
  clearConnectionHideTimer();
  connectionHideTimer = window.setTimeout(() => {
    connectionHideTimer = undefined;
    if (connectionDeleteControl.value?.id === connectionId) connectionDeleteControl.value = undefined;
  }, CONNECTION_DELETE_LEAVE_DELAY_MS);
}

function keepConnectionDeleteControlOpen() {
  clearConnectionHideTimer();
}

function deleteConnectionFromControl() {
  const connectionId = connectionDeleteControl.value?.id;
  if (connectionId) removeConnection(connectionId);
}

function openContextMenu(event: MouseEvent, state: Omit<ContextMenuState, 'x' | 'y'> = {}) {
  const bounds = canvas.value?.getBoundingClientRect();
  if (!bounds) return;
  event.preventDefault();
  event.stopPropagation();
  contextMenu.value = {
    ...state,
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top,
  };
}

function openCanvasContextMenu(event: MouseEvent) {
  const point = pointInWorld(event.clientX, event.clientY);
  openContextMenu(event, point ? { worldX: point.x, worldY: point.y } : {});
}

function openNodeContextMenu(event: MouseEvent, nodeId: string) {
  if (!nodes.value.some((node) => node.id === nodeId)) return;
  selectOnly(nodeId);
  clearConnectionState();
  openContextMenu(event, { nodeId });
}

function openConnectionContextMenu(event: MouseEvent, connectionId: string) {
  if (!connections.value.some((connection) => connection.id === connectionId)) return;
  clearConnectionDeleteControl();
  clearConnectionState();
  openContextMenu(event, { connectionId });
}

function startConnectionFromContext() {
  const nodeId = contextMenu.value?.nodeId;
  const node = nodeId ? nodeById.value.get(nodeId) : undefined;
  if (!node) return;
  selectOnly(node.id);
  connectionSourceId.value = node.id;
  connectionSourceImageIndex.value = node.type === 'image' ? primaryImageIndex(node) : undefined;
  connectionTargetId.value = undefined;
  connectionTargetPoint.value = undefined;
  connectionPointerActive.value = true;
  const sourcePoint = nodeOutputConnectionPoint(node, connectionSourceImageIndex.value);
  connectionPreview.value = {
    sourceId: node.id,
    x: sourcePoint.x,
    y: sourcePoint.y,
  };
  closeContextMenu();
}

function clearConnectionState() {
  connectionSourceId.value = undefined;
  connectionSourceImageIndex.value = undefined;
  connectionTargetId.value = undefined;
  connectionTargetPoint.value = undefined;
  connectionPreview.value = undefined;
  connectionPointerActive.value = false;
}

function beginConnection(event: PointerEvent, nodeId: string, requestedImageIndex?: number) {
  if (event.button !== 0) return;
  const node = nodeById.value.get(nodeId);
  if (!node) return;
  event.preventDefault();
  event.stopPropagation();
  closeContextMenu();
  selectOnly(nodeId);
  if (node.type === 'image' && requestedImageIndex !== undefined) {
    const index = Math.min(
      Math.max(0, Math.round(requestedImageIndex)),
      Math.max(0, imageResultUrls(node).length - 1),
    );
    node.primaryImageIndex = index;
    node.resultUrl = imageResultUrl(node, index) || node.resultUrl;
  }
  connectionSourceId.value = nodeId;
  connectionSourceImageIndex.value = node.type === 'image'
    ? primaryImageIndex(node)
    : undefined;
  connectionTargetId.value = undefined;
  connectionTargetPoint.value = undefined;
  connectionPointerActive.value = true;
  const sourcePoint = nodeOutputConnectionPoint(node, connectionSourceImageIndex.value);
  connectionPreview.value = {
    sourceId: nodeId,
    x: sourcePoint.x,
    y: sourcePoint.y,
  };
}

function finishConnection(event: PointerEvent, targetId: string) {
  const sourceId = connectionSourceId.value;
  if (!sourceId) return;
  event.preventDefault();
  event.stopPropagation();
  if (sourceId !== targetId) {
    connectNodes(sourceId, targetId, connectionSourceImageIndex.value);
  }
  selectOnly(targetId);
  clearConnectionState();
}

function setConnectionTarget(nodeId: string) {
  if (!connectionPointerActive.value || !connectionSourceId.value || connectionSourceId.value === nodeId) return;
  connectionTargetId.value = nodeId;
}

function clearConnectionTarget(nodeId: string) {
  if (connectionTargetId.value !== nodeId) return;
  connectionTargetId.value = undefined;
  connectionTargetPoint.value = undefined;
}

function updateConnectionTargetAtPointer(clientX: number, clientY: number) {
  if (!connectionPointerActive.value || !connectionSourceId.value) return;
  const element = document.elementFromPoint(clientX, clientY);
  const targetElement = element?.closest<HTMLElement>('[data-canvas-node-id]');
  const targetId = targetElement?.dataset.canvasNodeId;
  if (!targetId || targetId === connectionSourceId.value) {
    connectionTargetId.value = undefined;
    connectionTargetPoint.value = undefined;
    return;
  }

  const target = nodeById.value.get(targetId);
  const bounds = targetElement.getBoundingClientRect();
  if (!target || bounds.height <= 0 || viewport.value.zoom <= 0) return;

  const localY = (clientY - bounds.top) / viewport.value.zoom;
  const height = bounds.height / viewport.value.zoom;
  const edgePadding = Math.min(42, Math.max(22, height * 0.12));
  const offset = Math.max(edgePadding, Math.min(height - edgePadding, localY));
  connectionTargetId.value = targetId;
  connectionTargetPoint.value = { nodeId: targetId, offset };
}

function startCanvasPan(event: PointerEvent) {
  if (event.button !== 0 && event.button !== 1) return;
  const target = event.target as Element;
  if (target.closest('.canvas-node, .canvas-controls, .canvas-minimap, .canvas-empty-state, .connection-hint, .context-menu')) return;
  event.preventDefault();
  clearConnectionDeleteControl();
  closeContextMenu();
  const bounds = canvas.value?.getBoundingClientRect();
  if (event.button === 0 && event.shiftKey && bounds) {
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    selectionBox.value = {
      startX: x,
      startY: y,
      currentX: x,
      currentY: y,
      additive: true,
      initialIds: new Set(selectedIds.value),
    };
    return;
  }
  if (event.button === 0) clearSelection();
  panState.value = {
    clientX: event.clientX,
    clientY: event.clientY,
    x: viewport.value.x,
    y: viewport.value.y,
  };
}

function applyPointerPosition(clientX: number, clientY: number) {
  if (nodeDrag.value) {
    const drag = nodeDrag.value;
    const dx = (clientX - drag.clientX) / viewport.value.zoom;
    const dy = (clientY - drag.clientY) / viewport.value.zoom;
    for (const [nodeId, origin] of drag.origins) {
      const node = nodeById.value.get(nodeId);
      if (!node) continue;
      node.x = origin.x + dx;
      node.y = origin.y + dy;
    }
  }
  if (panState.value) {
    const pan = panState.value;
    viewport.value = {
      ...viewport.value,
      x: pan.x + clientX - pan.clientX,
      y: pan.y + clientY - pan.clientY,
    };
  }
  if (connectionPointerActive.value && connectionSourceId.value) {
    updateConnectionTargetAtPointer(clientX, clientY);
    const point = pointInWorld(clientX, clientY);
    if (point) connectionPreview.value = { sourceId: connectionSourceId.value, ...point };
  }
  if (selectionBox.value) {
    const bounds = canvas.value?.getBoundingClientRect();
    if (bounds) {
      selectionBox.value.currentX = clientX - bounds.left;
      selectionBox.value.currentY = clientY - bounds.top;
    }
  }
}

function movePointer(event: PointerEvent) {
  if (!nodeDrag.value && !panState.value && !selectionBox.value && !connectionPointerActive.value) return;
  pendingPointer = { clientX: event.clientX, clientY: event.clientY };
  if (pointerMoveFrame !== undefined) return;
  pointerMoveFrame = window.requestAnimationFrame(() => {
    pointerMoveFrame = undefined;
    const point = pendingPointer;
    pendingPointer = undefined;
    if (point) applyPointerPosition(point.clientX, point.clientY);
  });
}

function endPointerInteraction(event?: PointerEvent) {
  if (pointerMoveFrame !== undefined) {
    window.cancelAnimationFrame(pointerMoveFrame);
    pointerMoveFrame = undefined;
  }
  if (pendingPointer) {
    applyPointerPosition(pendingPointer.clientX, pendingPointer.clientY);
    pendingPointer = undefined;
  }
  const dragged = nodeDrag.value;
  const nodeMoved = Boolean(dragged && [...dragged.origins].some(([nodeId, origin]) => {
    const node = nodeById.value.get(nodeId);
    return Boolean(node && (Math.abs(node.x - origin.x) > 0.01 || Math.abs(node.y - origin.y) > 0.01));
  }));
  const pan = panState.value;
  const viewportMoved = Boolean(pan && (
    Math.abs(viewport.value.x - pan.x) > 0.01 || Math.abs(viewport.value.y - pan.y) > 0.01
  ));
  const completedSelectionBox = selectionBox.value;
  nodeDrag.value = undefined;
  panState.value = undefined;
  selectionBox.value = undefined;
  if (nodeMoved) commitCanvasHistory();
  else if (viewportMoved) markWorkflowDirty();
  if (completedSelectionBox) {
    const left = (Math.min(completedSelectionBox.startX, completedSelectionBox.currentX) - viewport.value.x) / viewport.value.zoom;
    const top = (Math.min(completedSelectionBox.startY, completedSelectionBox.currentY) - viewport.value.y) / viewport.value.zoom;
    const right = (Math.max(completedSelectionBox.startX, completedSelectionBox.currentX) - viewport.value.x) / viewport.value.zoom;
    const bottom = (Math.max(completedSelectionBox.startY, completedSelectionBox.currentY) - viewport.value.y) / viewport.value.zoom;
    const hits = nodes.value.filter((node) => {
      const size = layoutNodeDimensions(node);
      return node.x <= right && node.x + size.width >= left && node.y <= bottom && node.y + size.height >= top;
    }).map((node) => node.id);
    const next = completedSelectionBox.additive
      ? new Set([...completedSelectionBox.initialIds, ...hits])
      : new Set(hits);
    selectMany(next, { primaryId: hits.at(-1) });
  }
  if (connectionPointerActive.value) {
    if (event) updateConnectionTargetAtPointer(event.clientX, event.clientY);
    const sourceId = connectionSourceId.value;
    const targetId = connectionTargetId.value;
    if (sourceId && targetId && sourceId !== targetId) {
      connectNodes(sourceId, targetId, connectionSourceImageIndex.value);
      selectOnly(targetId);
    }
    clearConnectionState();
  }
}

function zoomCanvas(event: WheelEvent) {
  clearConnectionDeleteControl();
  const factor = Math.exp(-event.deltaY * 0.0015);
  setZoom(viewport.value.zoom * factor, event.clientX, event.clientY);
}

function changeZoom(delta: number) {
  const bounds = canvas.value?.getBoundingClientRect();
  setZoom(
    viewport.value.zoom + delta,
    bounds ? bounds.left + bounds.width / 2 : undefined,
    bounds ? bounds.top + bounds.height / 2 : undefined,
  );
}

function setZoom(nextZoom: number, clientX?: number, clientY?: number) {
  const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
  const bounds = canvas.value?.getBoundingClientRect();
  if (!bounds || clientX === undefined || clientY === undefined) {
    viewport.value = { ...viewport.value, zoom };
    scheduleViewportDirty();
    return;
  }

  const anchorX = clientX - bounds.left;
  const anchorY = clientY - bounds.top;
  const worldX = (anchorX - viewport.value.x) / viewport.value.zoom;
  const worldY = (anchorY - viewport.value.y) / viewport.value.zoom;
  viewport.value = {
    x: anchorX - worldX * zoom,
    y: anchorY - worldY * zoom,
    zoom,
  };
  scheduleViewportDirty();
}

function resetViewport(recordChange = true) {
  viewport.value = defaultViewport();
  if (recordChange) scheduleViewportDirty();
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('读取图片失败。'));
    reader.onerror = () => reject(new Error('读取图片失败。'));
    reader.readAsDataURL(file);
  });
}

async function addReference(file: File, nodeId = selectedId.value) {
  const node = nodeId ? nodeById.value.get(nodeId) : undefined;
  if (!node || node.type !== 'image') return;
  if (!capabilities.value.referenceImageUpload) {
    showToast('服务器尚未配置参考图存储。');
    return;
  }
  if (file.size > 15 * 1024 * 1024) {
    showToast('参考图不能超过 15 MB。');
    return;
  }
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    showToast('请选择 PNG、JPG 或 WEBP 图片。');
    return;
  }
  try {
    const dataUrl = await readFileAsDataUrl(file);
    const reference: ReferenceImage = {
      id: makeId('ref'),
      name: file.name,
      url: dataUrl,
      previewUrl: dataUrl,
      uploadState: 'uploading',
    };
    node.references.push(reference);
    const previousStatus = node.status;
    node.status = 'uploading';
    try {
      const result = await uploadCanvasImage(file);
      reference.url = result.url;
      reference.previewUrl = result.url;
      reference.uploadState = 'ready';
    } catch (error) {
      reference.uploadState = 'failed';
      node.error = `参考图上传失败：${messageFrom(error)}`;
    } finally {
      if (node.status === 'uploading') node.status = previousStatus;
      commitCanvasHistory();
    }
  } catch (error) {
    showToast(messageFrom(error));
  }
}

async function uploadNodeMedia(file: File, nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type === 'text') return;
  selectOnly(node.id);
  if (node.type === 'image') {
    await addReference(file, node.id);
    return;
  }
  if (!capabilities.value.videoUpload) {
    showToast('服务器尚未配置媒体存储。');
    return;
  }
  if (file.size > 300 * 1024 * 1024) {
    showToast('视频不能超过 300 MB。');
    return;
  }
  const supportedVideo = file.type.startsWith('video/')
    || /\.(mp4|webm|mov|m4v|avi|mkv)$/i.test(file.name);
  if (!supportedVideo) {
    showToast('请选择 MP4、WEBM、MOV、AVI 或 MKV 视频。');
    return;
  }
  const previousStatus = node.status;
  node.status = 'uploading';
  node.error = undefined;
  try {
    const result = await uploadCanvasVideo(file, activeWorkflowId.value);
    node.inputVideoId = result.videoId;
    node.inputUrl = result.url;
    node.resultUrl = undefined;
    node.sourceDuration = undefined;
    node.status = 'ready';
    mergeVideoAnalysisAsset(node, result);
    scheduleVideoAnalysisPolling(250);
    if (node.videoAnalysisStatus === 'failed') {
      showToast(`视频已上传，但解析未启动：${node.videoAnalysisError || '请重试。'}`);
    }
  } catch (error) {
    node.status = previousStatus;
    node.error = `视频上传失败：${messageFrom(error)}`;
  } finally {
    commitCanvasHistory();
  }
}

function removeReference(referenceId: string, nodeId = selectedId.value) {
  const node = nodeId ? nodeById.value.get(nodeId) : undefined;
  if (!node) return;
  const previousCount = node.references.length;
  node.references = node.references.filter((reference) => reference.id !== referenceId);
  if (node.references.length !== previousCount) commitCanvasHistory();
}

function directSourceNodes(nodeId: string) {
  return (incomingConnectionsByNode.value.get(nodeId) || [])
    .flatMap((connection) => {
      const source = nodeById.value.get(connection.from);
      return source ? [source] : [];
    });
}

function sourceImageIndexForTarget(source: CanvasNodeModel, targetId: string) {
  if (source.type !== 'image') return undefined;
  const connection = (incomingConnectionsByNode.value.get(targetId) || [])
    .find((item) => item.from === source.id);
  return normalizedImageIndex(source, connection?.sourceImageIndex);
}

function generationPrompt(
  node: CanvasNodeModel,
  directSources: CanvasNodeModel[],
  videoSpec?: VideoGenerationModelSpec,
) {
  let imageIndex = 0;
  const mediaContext = directSources
    .filter((item) => item.type === 'image' || item.type === 'video')
    .map((item) => {
      const role = item.type === 'image' ? videoFrameRole(videoSpec, imageIndex++) : '';
      const sourceImageIndex = sourceImageIndexForTarget(item, node.id);
      const resultLabel = sourceImageIndex !== undefined && imageResultUrls(item).length > 1
        ? ` · 图 ${sourceImageIndex + 1}`
        : '';
      const sourceLabel = `${mediaReferenceLabel(item)}${resultLabel}`;
      const label = role ? `${role} · ${sourceLabel}` : sourceLabel;
      const purpose = role
        ? `${role}画面`
        : item.type === 'image' ? '图片视觉参考' : '视频动作、节奏和运镜语义参考';
      const analysisLines = item.type === 'video' ? videoAnalysisPromptLines(item.videoAnalysis) : [];
      return [
        `- [${label}]：${analysisLines.length ? '已解析的视频内容参考' : purpose}`,
        ...analysisLines.map((line) => `  ${line}`),
      ].join('\n');
    });
  const textContext = directSources
    .filter((item) => item.type === 'text' && item.content.trim())
    .map((item) => `- ${item.title}：${item.content.trim()}`);
  return [
    `创作要求：\n${node.prompt.trim()}`,
    mediaContext.length ? `引用素材（标签与画布一致）：\n${mediaContext.join('\n')}` : '',
    textContext.length ? `直接连接的文本要求：\n${textContext.join('\n')}` : '',
  ].filter(Boolean).join('\n\n');
}

function videoReferenceReadinessError(directSources: CanvasNodeModel[]) {
  for (const source of directSources) {
    if (source.type !== 'video' || !source.inputVideoId) continue;
    const status = normalizeVideoAnalysisStatus(source.videoAnalysisStatus);
    if (status === 'failed') {
      return `${source.title} 解析失败，请在该视频节点重试解析后再生成。`;
    }
    if (status !== 'ready') {
      return `${source.title} 正在后台解析，解析完成后再生成。`;
    }
  }
  return '';
}

function generationReferences(node: CanvasNodeModel, directSources: CanvasNodeModel[]) {
  const values = [
    ...node.references.filter((item) => item.uploadState === 'ready').map((item) => item.url),
    ...directSources.flatMap((item) => {
      if (item.type !== 'image') return [];
      const resultUrl = imageResultUrl(item, sourceImageIndexForTarget(item, node.id));
      if (resultUrl) return [resultUrl];
      return item.references
        .filter((reference) => reference.uploadState === 'ready')
        .map((reference) => reference.url);
    }),
  ];
  return Array.from(new Set(values.filter((value): value is string => Boolean(value && /^https?:\/\//i.test(value)))));
}

function videoGenerationMode(node: CanvasNodeModel): 'text_to_video' | 'image_to_video' {
  const hasImageInput = node.references.length > 0
    || directSourceNodes(node.id).some((item) => item.type === 'image');
  if (hasImageInput) return 'image_to_video';
  const selectedModel = modelOptions.value.videoModels.find((item) => item.id === node.model);
  if (selectedModel?.modes.includes('image_to_video') && !selectedModel.modes.includes('text_to_video')) {
    return 'image_to_video';
  }
  return 'text_to_video';
}

function videoModelSpec(node: CanvasNodeModel, mode = videoGenerationMode(node)): VideoGenerationModelSpec | undefined {
  const available = modelOptions.value.videoModels.filter((item) => item.modes.includes(mode));
  return available.find((item) => item.id === node.model) || available[0];
}

function syncVideoNodeOptions(node: CanvasNodeModel, spec: VideoGenerationModelSpec) {
  node.model = spec.id;
  if (!spec.aspect_ratios.includes(node.aspectRatio)) {
    node.aspectRatio = spec.aspect_ratios[0] || '16:9';
  }
  if (!spec.durations.includes(node.duration)) {
    node.duration = spec.default_duration ?? spec.durations[0] ?? 5;
  }
  if (!spec.options.includes(node.quality)) {
    node.quality = spec.default_option || spec.options[0] || '';
  }
}

function updateTaskProgress(node: CanvasNodeModel, task: { progress?: string; cost?: number }) {
  node.progress = task.progress || '生成中';
  if (typeof task.cost === 'number') node.cost = task.cost;
}

async function downloadNodeImages(nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type !== 'image' || !node.taskId || downloadingNodeId.value) return;
  const count = node.resultUrls?.filter(Boolean).length || (node.resultUrl ? 1 : node.imageCount);
  downloadingNodeId.value = node.id;
  try {
    await downloadCanvasImages(node.taskId, count);
    showToast('图片下载已开始。');
  } catch (error) {
    showToast(messageFrom(error));
  } finally {
    if (downloadingNodeId.value === node.id) downloadingNodeId.value = undefined;
  }
}

async function downloadNodeImage(nodeId: string, imageIndex: number) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type !== 'image' || !node.taskId || downloadingImageKey.value) return;
  const key = `${node.id}:${imageIndex}`;
  downloadingImageKey.value = key;
  try {
    await downloadCanvasImage(node.taskId, imageIndex);
    showToast(`第 ${imageIndex + 1} 张图片下载已开始。`);
  } catch (error) {
    showToast(messageFrom(error));
  } finally {
    if (downloadingImageKey.value === key) downloadingImageKey.value = undefined;
  }
}

async function generateNode(nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type === 'text') return;
  if (!node.prompt.trim()) {
    node.error = '请先填写提示词。';
    return;
  }
  const directSources = directSourceNodes(node.id);
  const videoReferenceError = videoReferenceReadinessError(directSources);
  if (videoReferenceError) {
    node.error = videoReferenceError;
    return;
  }
  const usableReferences = generationReferences(node, directSources);
  if (node.references.some((reference) => reference.uploadState === 'failed')) {
    showToast('上传失败的参考图已跳过。');
  }
  const videoMode = node.type === 'video' ? videoGenerationMode(node) : undefined;
  const selectedVideoSpec = node.type === 'video' ? videoModelSpec(node, videoMode) : undefined;
  const model = node.type === 'image'
    ? node.model || modelOptions.value.imageModel
    : selectedVideoSpec?.id || '';
  if (!model) {
    node.error = node.type === 'video' ? '当前模式没有可用的视频模型。' : '当前没有可用的图片模型。';
    return;
  }
  if (node.type === 'video' && selectedVideoSpec) {
    syncVideoNodeOptions(node, selectedVideoSpec);
    if (videoMode === 'image_to_video') {
      const referenceCount = usableReferences.length;
      if (videoModelUsesFirstLastFrames(selectedVideoSpec)) {
        if (referenceCount < 1) {
          node.error = `${selectedVideoSpec.label} 请先连接或引用一张首帧图片。`;
          return;
        }
        if (referenceCount > 2) {
          node.error = `${selectedVideoSpec.label} 最多引用首帧和尾帧，共 2 张图片。`;
          return;
        }
      } else {
        if (referenceCount < selectedVideoSpec.min_images) {
          node.error = `${selectedVideoSpec.label} 至少需要 ${selectedVideoSpec.min_images} 张已生成或已上传的参考图。`;
          return;
        }
        if (referenceCount > selectedVideoSpec.max_images) {
          node.error = `${selectedVideoSpec.label} 最多支持 ${selectedVideoSpec.max_images} 张参考图，当前有 ${referenceCount} 张。`;
          return;
        }
      }
    }
  }
  commitCanvasHistory();
  taskControllers.get(node.id)?.abort();
  const controller = new AbortController();
  taskControllers.set(node.id, controller);
  const taskId = node.status === 'generating' && node.taskId ? node.taskId : makeId(`${node.type}_task`);
  node.taskId = taskId;
  node.model = model;
  node.status = 'generating';
  node.progress = '正在提交';
  node.error = undefined;
  replaceCanvasHistoryCurrent();
  try {
    const result = node.type === 'image'
      ? await generateCanvasImage({
        taskId,
        prompt: generationPrompt(node, directSources),
        model,
        aspectRatio: node.aspectRatio,
        imageSize: node.imageSize,
        quality: node.quality,
        thinkingLevel: node.thinkingLevel,
        imageCount: node.imageCount,
        references: usableReferences,
        workflowId: activeWorkflowId.value,
        nodeId: node.id,
        signal: controller.signal,
        onProgress: (task) => updateTaskProgress(node, task),
      })
      : await generateCanvasVideo({
        taskId,
        prompt: generationPrompt(node, directSources, selectedVideoSpec),
        model,
        mode: videoMode || 'text_to_video',
        aspectRatio: node.aspectRatio,
        duration: node.duration,
        quality: node.quality,
        references: usableReferences,
        workflowId: activeWorkflowId.value,
        nodeId: node.id,
        signal: controller.signal,
        onProgress: (task) => updateTaskProgress(node, task),
      });
    node.resultUrls = node.type === 'image' ? result.resultUrls : undefined;
    node.primaryImageIndex = node.type === 'image' ? 0 : undefined;
    node.galleryExpanded = false;
    node.resultUrl = node.type === 'image' ? result.resultUrls[0] || result.resultUrl : result.resultUrl;
    node.taskId = result.taskId;
    node.cost = result.cost;
    node.progress = '已完成';
    node.status = 'ready';
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    node.status = 'failed';
    node.progress = undefined;
    node.error = messageFrom(error);
  } finally {
    if (taskControllers.get(node.id) === controller) taskControllers.delete(node.id);
    replaceCanvasHistoryCurrent();
  }
}

async function resumeNodeTask(nodeId: string) {
  const node = nodeById.value.get(nodeId);
  if (!node || node.type === 'text' || !node.taskId) return;
  taskControllers.get(node.id)?.abort();
  const controller = new AbortController();
  taskControllers.set(node.id, controller);
  node.status = 'generating';
  node.progress = '正在恢复任务';
  node.error = undefined;
  replaceCanvasHistoryCurrent();
  try {
    const result = await resumeCanvasGeneration({
      kind: node.type,
      taskId: node.taskId,
      signal: controller.signal,
      onProgress: (task) => updateTaskProgress(node, task),
    });
    node.resultUrls = node.type === 'image' ? result.resultUrls : undefined;
    node.primaryImageIndex = node.type === 'image' ? 0 : undefined;
    node.galleryExpanded = false;
    node.resultUrl = node.type === 'image' ? result.resultUrls[0] || result.resultUrl : result.resultUrl;
    node.taskId = result.taskId;
    node.cost = result.cost;
    node.progress = '已完成';
    node.status = 'ready';
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    node.status = 'failed';
    node.progress = undefined;
    node.error = messageFrom(error);
  } finally {
    if (taskControllers.get(node.id) === controller) taskControllers.delete(node.id);
    replaceCanvasHistoryCurrent();
  }
}

function serializeNode(node: CanvasNodeModel) {
  const persistentNode = { ...node };
  delete persistentNode.galleryExpanded;
  return {
    ...persistentNode,
    resultUrls: node.resultUrls?.filter(Boolean),
    references: node.references.map((reference) => ({
      id: reference.id,
      name: reference.name,
      url: reference.url,
      previewUrl: reference.url,
      uploadState: reference.uploadState,
    })),
  };
}

function legacyImageSize(value: unknown) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === '0.5k' || normalized === '512x512') return '0.5K';
  if (normalized === '2k' || normalized.startsWith('2048x')) return '2K';
  if (normalized === '4k' || normalized.startsWith('4096x')) return '4K';
  return '1K';
}

function normalizeCanvasNode(node: CanvasNodeModel): CanvasNodeModel {
  const legacy = node as CanvasNodeModel & { resolution?: string };
  const resultUrls = (Array.isArray(node.resultUrls) ? node.resultUrls : [])
    .filter((value): value is string => Boolean(value));
  if (!resultUrls.length && node.resultUrl) resultUrls.push(node.resultUrl);
  const rawImageCount = Number(node.imageCount);
  const imageCount = Number.isInteger(rawImageCount) && rawImageCount >= 1 && rawImageCount <= 4
    ? rawImageCount
    : Math.min(4, Math.max(1, resultUrls.length || 1));
  const imageModel = node.type === 'image' && modelOptions.value.imageModels.includes(node.model || '')
    ? node.model
    : node.type === 'image'
      ? modelOptions.value.imageModel
      : node.model;
  const requestedPrimaryIndex = Number(node.primaryImageIndex);
  const primaryImageIndex = Number.isInteger(requestedPrimaryIndex)
    && requestedPrimaryIndex >= 0
    && requestedPrimaryIndex < resultUrls.length
    ? requestedPrimaryIndex
    : 0;
  const videoAnalysis = node.type === 'video'
    ? normalizeCanvasVideoAnalysis(node.videoAnalysis)
    : undefined;
  const videoAnalysisStatus = node.type === 'video' && node.inputVideoId
    ? normalizeVideoAnalysisStatus(node.videoAnalysisStatus || (videoAnalysis ? 'ready' : 'pending'))
    : undefined;
  return {
    ...node,
    model: imageModel,
    aspectRatio: node.aspectRatio || '1:1',
    imageSize: node.imageSize || legacyImageSize(legacy.resolution),
    quality: node.quality || 'auto',
    thinkingLevel: node.thinkingLevel || 'minimal',
    imageCount,
    resultUrls,
    primaryImageIndex,
    galleryExpanded: false,
    resultUrl: resultUrls[primaryImageIndex] || node.resultUrl,
    references: Array.isArray(node.references) ? node.references : [],
    videoAnalysis,
    videoAnalysisStatus,
    videoAnalysisError: node.type === 'video' ? node.videoAnalysisError : undefined,
    status: node.status === 'uploading' ? 'failed' : node.status,
    error: node.status === 'uploading' ? '上传被中断，请重新选择素材。' : node.error,
  };
}

function workflowSnapshot(): WorkflowDocument {
  const document: WorkflowDocument = {
    id: activeWorkflowId.value,
    revision: workflowRevision.value,
    title: workflowTitle.value.trim() || '未命名画布',
    createdAt: workflowCreatedAt.value,
    nodes: nodes.value.map(serializeNode),
    connections: connections.value.map((connection) => ({ ...connection })),
    viewport: { ...viewport.value },
    assistantMessages: chatMessages.value,
    timeline: timeline.value,
    latestComposition: latestComposition.value,
  };
  return JSON.parse(JSON.stringify(document)) as WorkflowDocument;
}

async function persistWorkflowSnapshot(snapshot: WorkflowDocument) {
  const result = await canvasApi.saveWorkflow(snapshot);
  if (activeWorkflowId.value !== snapshot.id) return;
  activeWorkflowId.value = result.id;
  workflowRevision.value = Number(result.revision || 0);
  workflowCreatedAt.value = result.createdAt || snapshot.createdAt;
  if (workflowPanelOpen.value && !deletingWorkflows.value) void loadWorkflows();
}

async function saveWorkflow(silent = false) {
  const saved = await saveWorkflowNow();
  if (!silent) showToast(saved ? '画布已保存。' : workflowSaveError.value || '画布保存失败。');
  return saved;
}

async function flushPendingWorkflowChanges() {
  await nextTick();
  return flushWorkflowSave();
}

async function resetWorkspace(force = false, discardCurrent = false) {
  const discardingConflict = workflowSaveBlocked.value;
  if (!force && (nodes.value.length || workflowDirty.value)) {
    const message = discardingConflict
      ? '画布已在其他页面更新，本页修改无法安全保存。放弃本页修改并新建画布吗？'
      : '新建画布会先保存当前修改，再打开空白画布。继续吗？';
    if (!window.confirm(message)) return;
  }
  if (!discardCurrent && !discardingConflict && !(await flushPendingWorkflowChanges())) {
    showToast(workflowSaveError.value || '当前画布保存失败，暂未新建画布。');
    return;
  }

  pauseWorkflowAutosave();
  stopVideoAnalysisPolling();
  resetWorkflowAutosaveContext();
  taskControllers.forEach((controller) => controller.abort());
  taskControllers.clear();
  clearConnectionDeleteControl();
  nodes.value = [];
  connections.value = [];
  canvasDocumentRevision.value += 1;
  clearSelection();
  clearConnectionState();
  workflowTitle.value = '未命名画布';
  activeWorkflowId.value = makeId('workflow');
  workflowRevision.value = 0;
  workflowCreatedAt.value = undefined;
  chatMessages.value = [];
  timeline.value = undefined;
  latestComposition.value = undefined;
  retryingVideoAnalysisIds.value = new Set();
  resetViewport(false);
  await nextTick();
  resetCanvasHistory();
  resumeWorkflowAsSaved();
}

async function loadWorkflows() {
  const requestEpoch = ++workflowLoadEpoch;
  loadingWorkflows.value = true;
  try {
    const result = await canvasApi.listWorkflows();
    if (requestEpoch !== workflowLoadEpoch) return;
    workflows.value = result.items;
  } catch (error) {
    if (requestEpoch !== workflowLoadEpoch) return;
    showToast(messageFrom(error));
  } finally {
    if (requestEpoch === workflowLoadEpoch) loadingWorkflows.value = false;
  }
}

async function openWorkflow(summary: WorkflowSummary) {
  const reopeningCurrent = summary.id === activeWorkflowId.value;
  if (reopeningCurrent && !workflowSaveBlocked.value) {
    workflowPanelOpen.value = false;
    return;
  }
  const discardingConflict = workflowSaveBlocked.value;
  if (nodes.value.length || workflowDirty.value) {
    const message = discardingConflict
      ? `画布版本已冲突。重新打开“${summary.title}”会放弃本页未保存修改，继续吗？`
      : `打开“${summary.title}”会替换当前画布，继续吗？`;
    if (!window.confirm(message)) return;
  }
  try {
    const workflow = await canvasApi.getWorkflow(summary.id);
    if (!discardingConflict && !(await flushPendingWorkflowChanges())) {
      showToast(workflowSaveError.value || '当前画布保存失败，暂未切换画布。');
      return;
    }
    pauseWorkflowAutosave();
    stopVideoAnalysisPolling();
    resetWorkflowAutosaveContext();
    taskControllers.forEach((controller) => controller.abort());
    taskControllers.clear();
    clearConnectionDeleteControl();
    activeWorkflowId.value = workflow.id;
    workflowRevision.value = Number(workflow.revision || 0);
    workflowTitle.value = workflow.title || '未命名画布';
    workflowCreatedAt.value = workflow.createdAt;
    nodes.value = (workflow.nodes || []).map(normalizeCanvasNode);
    connections.value = (workflow.connections || []).map((connection) => {
      const source = nodeById.value.get(connection.from);
      if (!source || source.type !== 'image') return connection;
      return {
        ...connection,
        sourceImageIndex: normalizedImageIndex(source, connection.sourceImageIndex),
      };
    });
    chatMessages.value = workflow.assistantMessages || [];
    timeline.value = workflow.timeline;
    latestComposition.value = workflow.latestComposition;
    viewport.value = workflow.viewport || defaultViewport();
    canvasDocumentRevision.value += 1;
    clearSelection();
    workflowPanelOpen.value = false;
    await nextTick();
    resetCanvasHistory();
    const updatedAt = Date.parse(workflow.updatedAt || '');
    resumeWorkflowAsSaved(Number.isFinite(updatedAt) ? new Date(updatedAt) : undefined);
    scheduleVideoAnalysisPolling(250);
    for (const node of nodes.value.filter((item) => (
      item.status === 'generating' || (item.status === 'ready' && !item.resultUrl)
    ) && item.taskId && item.type !== 'text')) {
      void resumeNodeTask(node.id);
    }
  } catch (error) {
    showToast(messageFrom(error));
  }
}

async function deleteWorkflowRecords(
  summaries: WorkflowSummary[],
  options: { batch: boolean; confirmation: string },
) {
  if (deletingWorkflows.value) return;
  const unique = [...new Map(summaries.map((summary) => [summary.id, summary])).values()];
  if (!unique.length || !window.confirm(options.confirmation)) return;
  const requestedIds = unique.map((summary) => summary.id);
  const deletingActiveWorkflow = requestedIds.includes(activeWorkflowId.value);
  deletingWorkflows.value = true;
  workflowLoadEpoch += 1;
  loadingWorkflows.value = false;
  if (deletingActiveWorkflow) {
    if (!workflowSaveBlocked.value && !(await flushPendingWorkflowChanges())) {
      showToast(workflowSaveError.value || '当前画布保存失败，暂未删除。');
      deletingWorkflows.value = false;
      return;
    }
    pauseWorkflowAutosave();
  }
  try {
    let deletedIds: string[];
    if (options.batch) {
      deletedIds = (await canvasApi.deleteWorkflows(requestedIds)).deletedIds;
    } else {
      await canvasApi.deleteWorkflow(requestedIds[0]);
      deletedIds = requestedIds;
    }
    const deletedIdSet = new Set(deletedIds);
    workflows.value = workflows.value.filter((item) => !deletedIdSet.has(item.id));
    if (deletingActiveWorkflow && deletedIdSet.has(activeWorkflowId.value)) {
      await resetWorkspace(true, true);
    } else if (deletingActiveWorkflow) {
      resumeWorkflowAutosave();
    }
    showToast(deletedIds.length === 1
      ? '画布已永久删除。'
      : `已永久删除 ${deletedIds.length} 个画布。`);
  } catch (error) {
    if (deletingActiveWorkflow) {
      resumeWorkflowAutosave();
      markWorkflowDirty();
    }
    showToast(messageFrom(error));
  } finally {
    deletingWorkflows.value = false;
  }
}

async function deleteWorkflow(summary: WorkflowSummary) {
  await deleteWorkflowRecords([summary], {
    batch: false,
    confirmation: `确定永久删除画布“${summary.title}”吗？删除后无法恢复。`,
  });
}

async function deleteWorkflowBatch(summaries: WorkflowSummary[]) {
  const includesActive = summaries.some((summary) => summary.id === activeWorkflowId.value);
  await deleteWorkflowRecords(summaries, {
    batch: true,
    confirmation: [
      `确定永久删除选中的 ${summaries.length} 个画布吗？删除后无法恢复。`,
      includesActive ? '其中包含当前画布，删除后将打开空白画布。' : '',
    ].filter(Boolean).join('\n'),
  });
}

function toggleWorkflowPanel() {
  workflowPanelOpen.value = !workflowPanelOpen.value;
  if (workflowPanelOpen.value) void loadWorkflows();
}

function newChat() {
  if (chatMessages.value.length && !window.confirm('清空当前画布中的助手对话吗？')) return;
  chatMessages.value = [];
  chatInput.value = '';
}

function canvasContext() {
  const nodeLines = nodes.value.map((node) => {
    const content = node.type === 'text' ? node.content : node.prompt;
    const analysis = node.type === 'video' ? videoAnalysisPromptLines(node.videoAnalysis) : [];
    return [
      `${node.title} [${node.type}, ${node.status}]${content.trim() ? `: ${content.trim()}` : ''}`,
      ...analysis.map((line) => `  ${line}`),
    ].join('\n');
  });
  const connectionLines = connections.value.map((connection) => {
    const from = nodeById.value.get(connection.from)?.title || connection.from;
    const to = nodeById.value.get(connection.to)?.title || connection.to;
    return `${from} -> ${to}`;
  });
  return [...nodeLines, ...(connectionLines.length ? ['连接关系：', ...connectionLines] : [])].join('\n').slice(0, 30_000);
}

async function sendChat() {
  const message = chatInput.value.trim();
  if (!message || chatSending.value) return;
  const userMessage: ChatMessage = { role: 'user', content: message, timestamp: new Date().toISOString() };
  chatMessages.value.push(userMessage);
  markWorkflowDirty();
  chatInput.value = '';
  chatSending.value = true;
  try {
    const result = await canvasApi.sendAssistant({
      message,
      history: chatMessages.value.slice(0, -1),
      canvasContext: canvasContext(),
      workflowId: activeWorkflowId.value,
      nodeId: selectedId.value,
    });
    chatMessages.value.push({ role: 'assistant', content: result.response, timestamp: new Date().toISOString() });
    markWorkflowDirty();
  } catch (error) {
    chatMessages.value.push({ role: 'assistant', content: `请求失败：${messageFrom(error)}` });
    markWorkflowDirty();
  } finally {
    chatSending.value = false;
  }
}

async function refreshHealth() {
  try {
    const runtime = await loadCanvasRuntime();
    capabilities.value = runtime.capabilities;
    modelOptions.value = runtime.models;
    backendOnline.value = true;
  } catch {
    backendOnline.value = false;
  }
}

function toggleAssistant() {
  assistantOpen.value = !assistantOpen.value;
}

function addStoryboardPrompt(prompt: string) {
  const placement = nodePlacement('image');
  const node = createNode('image', placement?.x, placement?.y);
  node.title = '分镜图';
  node.prompt = prompt;
  nodes.value.push(node);
  selectOnly(node.id);
  commitCanvasHistory();
  fitRecentNodesInViewport();
  void generateNode(node.id);
}

async function saveTimeline(timelineDocument: TimelineDocument, composition?: CompositionTask) {
  timeline.value = timelineDocument;
  latestComposition.value = composition;
  await saveWorkflow(true);
}

function handleKeyboard(event: KeyboardEvent) {
  const target = event.target as HTMLElement;
  const editingText = target.matches('input, textarea, select') || target.isContentEditable;
  const modifier = event.ctrlKey || event.metaKey;
  if (!editingText && modifier && !event.altKey && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) redoCanvasChange();
    else undoCanvasChange();
    return;
  }
  if (!editingText && modifier && !event.altKey && event.key.toLowerCase() === 'y') {
    event.preventDefault();
    redoCanvasChange();
    return;
  }
  if (!editingText && modifier && !event.altKey && event.key.toLowerCase() === 'a') {
    event.preventDefault();
    selectMany(nodes.value.map((node) => node.id), { primaryId: nodes.value.at(-1)?.id });
    return;
  }
  if (!editingText && modifier && !event.altKey && event.key.toLowerCase() === 'c') {
    event.preventDefault();
    copySelectedNodes();
    return;
  }
  if (!editingText && modifier && !event.altKey && event.key.toLowerCase() === 'v') {
    event.preventDefault();
    pasteCanvasClipboard();
    return;
  }
  if (editingText) return;
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedIds.value.size) {
    event.preventDefault();
    void removeSelectedNodes();
  }
  if (event.key === 'Escape') {
    closeContextMenu();
    clearConnectionState();
    assistantOpen.value = false;
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!workflowDirty.value && !savingWorkflow.value) return;
  event.preventDefault();
  event.returnValue = '';
}

onBeforeRouteLeave(async () => {
  if (await flushPendingWorkflowChanges()) return true;
  return window.confirm('当前画布自动保存失败，仍要离开吗？');
});

onMounted(() => {
  if (canvas.value) {
    canvas.value.scrollLeft = 0;
    canvas.value.scrollTop = 0;
  }
  resetCanvasHistory();
  resumeWorkflowAsSaved();
  void refreshHealth();
  window.addEventListener('pointermove', movePointer);
  window.addEventListener('pointerup', endPointerInteraction);
  window.addEventListener('pointerdown', closeContextMenu);
  window.addEventListener('keydown', handleKeyboard);
  window.addEventListener('beforeunload', handleBeforeUnload);
});

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', movePointer);
  window.removeEventListener('pointerup', endPointerInteraction);
  window.removeEventListener('pointerdown', closeContextMenu);
  window.removeEventListener('keydown', handleKeyboard);
  window.removeEventListener('beforeunload', handleBeforeUnload);
  stopVideoAnalysisPolling();
  taskControllers.forEach((controller) => controller.abort());
  taskControllers.clear();
  clearConnectionDeleteControl();
  if (viewportDirtyTimer !== undefined) window.clearTimeout(viewportDirtyTimer);
  if (pointerMoveFrame !== undefined) window.cancelAnimationFrame(pointerMoveFrame);
});
</script>

<template>
  <main class="infinite-canvas-root canvas-app-shell">
    <header class="topbar">
      <div class="canvas-heading">
        <RouterLink class="icon-button canvas-back-button" to="/image" title="返回首页" aria-label="返回首页">
          <ArrowLeft :size="18" aria-hidden="true" />
        </RouterLink>
        <span class="canvas-heading-icon"><GitBranch :size="17" aria-hidden="true" /></span>
        <div>
          <strong>无限画布</strong>
          <span>{{ nodes.length }} 个节点</span>
        </div>
      </div>

      <div class="title-control">
        <input v-model="workflowTitle" aria-label="工作流名称" />
        <span :class="['backend-status', { offline: !backendOnline }]">
          <i aria-hidden="true" />
          {{ backendOnline ? '服务已连接' : '等待服务' }}
        </span>
        <span
          class="workflow-save-status"
          :class="{ saving: savingWorkflow, dirty: workflowDirty, error: workflowSaveError }"
          :title="workflowSaveTitle"
          aria-live="polite"
        >{{ workflowSaveLabel }}</span>
      </div>

      <div class="topbar-actions">
        <button class="icon-button" type="button" title="画布记录" aria-label="打开画布记录" :aria-expanded="workflowPanelOpen" @click="toggleWorkflowPanel"><FolderOpen :size="18" /></button>
        <button class="icon-button" type="button" title="新建画布" aria-label="新建画布" @click="resetWorkspace()"><Plus :size="18" /></button>
        <button class="icon-button" type="button" title="撤销" aria-label="撤销" :disabled="!canUndoCanvas || hasActiveCanvasOperations()" @click="undoCanvasChange"><Undo2 :size="18" /></button>
        <button class="icon-button" type="button" title="恢复" aria-label="恢复" :disabled="!canRedoCanvas || hasActiveCanvasOperations()" @click="redoCanvasChange"><Redo2 :size="18" /></button>
        <button class="icon-button" type="button" :title="workflowSaveTitle" aria-label="保存工作流" :disabled="savingWorkflow" @click="saveWorkflow()">
          <LoaderCircle v-if="savingWorkflow" class="spin" :size="18" />
          <Save v-else :size="18" />
        </button>
        <button class="icon-button" type="button" title="故事板" aria-label="故事板" @click="storyboardOpen = true"><Clapperboard :size="18" /></button>
        <button class="icon-button" type="button" title="时间线" aria-label="打开时间线工作台" @click="timelineOpen = true"><Film :size="18" /></button>
      </div>
    </header>

    <div class="workspace-layout">
      <section
        ref="canvas"
        class="canvas-surface"
        :class="{ 'is-panning': Boolean(panState), 'is-selecting': Boolean(selectionBox), 'is-connection-mode': connectionPointerActive }"
        :style="canvasStyle"
        aria-label="工作流画布"
        @pointerdown="startCanvasPan"
        @wheel.prevent="zoomCanvas"
        @contextmenu.prevent="openCanvasContextMenu"
      >
        <svg class="connection-layer" aria-hidden="true">
          <g v-for="line in connectionLines" :key="line.id">
            <path
              class="connection-hitbox"
              :d="connectionPath(line)"
              @pointerenter="enterConnectionHover($event, line.id)"
              @pointermove="moveConnectionHover($event, line.id)"
              @pointerleave="leaveConnectionHover(line.id)"
              @contextmenu.stop.prevent="openConnectionContextMenu($event, line.id)"
            />
            <path class="connection-path" :d="connectionPath(line)" />
            <path class="connection-flow-path" :d="connectionPath(line)" pathLength="1" />
          </g>
          <path v-if="previewConnectionLine" class="connection-preview-path" :d="connectionPath(previewConnectionLine)" />
          <path v-if="previewConnectionLine" class="connection-preview-flow-path" :d="connectionPath(previewConnectionLine)" pathLength="1" />
        </svg>

        <span
          v-if="previewConnectionLine && !connectionTargetId"
          class="connection-preview-port"
          aria-hidden="true"
          :style="connectionPreviewPortStyle"
        ><span /></span>

        <div v-if="selectionBox" class="canvas-selection-box" :style="selectionBoxStyle" aria-hidden="true" />

        <Transition name="connection-delete">
          <button
            v-if="connectionDeleteControl"
            class="connection-delete-button"
            type="button"
            title="删除连接"
            aria-label="删除连接"
            :style="connectionDeleteControlStyle"
            @pointerdown.stop.prevent
            @pointerenter="keepConnectionDeleteControlOpen"
            @pointerleave="leaveConnectionHover(connectionDeleteControl.id)"
            @click.stop="deleteConnectionFromControl"
          >
            <Scissors :size="21" aria-hidden="true" />
          </button>
        </Transition>

        <div class="canvas-world" :style="worldStyle" :data-rendered-node-count="visibleNodes.length">
          <CanvasNode
            v-for="node in visibleNodes"
            :key="node.id"
            :node="node"
            :node-width="nodeDimensions(node).width"
            :stage-height="mediaStageHeight(node)"
            :node-height="nodeDimensions(node).height"
            :prompt-width="canvasPromptWidth"
            :zoom="viewport.zoom"
            :connection-offset="nodeConnectionOffset(node)"
            :selected="selectedIds.has(node.id)"
            :primary-selected="selectedId === node.id"
            :connection-source="connectionSourceId === node.id"
            :connection-image-index="connectionSourceId === node.id ? connectionSourceImageIndex : undefined"
            :dragging="Boolean(nodeDrag && selectedIds.has(node.id))"
            :connection-target="connectionTargetId === node.id"
            :connection-target-offset="connectionTargetPoint?.nodeId === node.id ? connectionTargetPoint.offset : undefined"
            :image-enabled="capabilities.image"
            :video-enabled="capabilities.video"
            :upload-enabled="node.type === 'image' ? capabilities.referenceImageUpload : capabilities.videoUpload"
            :image-models="modelOptions.imageModels"
            :video-models="modelOptions.videoModels"
            :video-mode="node.type === 'video' ? videoGenerationMode(node) : 'text_to_video'"
            :prompt-references="promptReferences(node.id)"
            :reference-candidates="selectedId === node.id ? promptReferenceCandidates(node.id) : []"
            :downloading="downloadingNodeId === node.id"
            :downloading-image-index="downloadingImageKey?.startsWith(`${node.id}:`) ? Number(downloadingImageKey.split(':')[1]) : undefined"
            :retrying-video-analysis="Boolean(node.inputVideoId && retryingVideoAnalysisIds.has(node.inputVideoId))"
            @pointerdown="startNodeInteraction($event, node.id)"
            @select="selectNode(node.id)"
            @update-prompt="updateNodePrompt(node.id, $event)"
            @update-content="updateCanvasNode(node.id, { key: 'content', value: $event })"
            @update-node="updateCanvasNode(node.id, $event)"
            @set-primary-image="setPrimaryImage(node.id, $event)"
            @toggle-gallery="toggleImageGallery(node.id)"
            @generate="generateNode(node.id)"
            @upload-media="uploadNodeMedia($event, node.id)"
            @remove-reference="removeReference($event, node.id)"
            @add-prompt-reference="addPromptReference(node.id, $event)"
            @remove-prompt-reference="removePromptReference(node.id, $event)"
            @download="downloadNodeImages(node.id)"
            @download-image="downloadNodeImage(node.id, $event)"
            @retry-video-analysis="retryVideoAnalysis(node.id)"
            @context-menu="openNodeContextMenu($event, node.id)"
            @start-connection="(event, imageIndex) => beginConnection(event, node.id, imageIndex)"
            @finish-connection="finishConnection($event, node.id)"
            @connection-target-enter="setConnectionTarget(node.id)"
            @connection-target-leave="clearConnectionTarget(node.id)"
            @remove="removeNode(node.id)"
          />
        </div>

        <div
          v-if="contextMenu"
          class="context-menu"
          role="menu"
          :style="contextMenuStyle"
          @pointerdown.stop
          @contextmenu.prevent
        >
          <template v-if="contextMenu.nodeId">
            <p class="context-menu-heading">{{ contextMenu.nodeId ? nodeById.get(contextMenu.nodeId)?.title : '' }}</p>
            <button type="button" role="menuitem" @click="startConnectionFromContext"><Link2 :size="15" /> 开始连接</button>
            <button type="button" role="menuitem" @click="duplicateContextNode"><Copy :size="15" /> 复制节点</button>
            <button class="danger" type="button" role="menuitem" @click="removeContextNode"><Trash2 :size="15" /> 删除节点</button>
          </template>
          <template v-else-if="contextMenu.connectionId">
            <p class="context-menu-heading">连接线</p>
            <button class="danger" type="button" role="menuitem" @click="removeContextConnection"><Trash2 :size="15" /> 删除连接</button>
          </template>
          <template v-else>
            <p class="context-menu-heading">添加节点</p>
            <button type="button" role="menuitem" @click="addNodeFromContext('image')"><ImageIcon :size="15" /> 图片节点</button>
            <button type="button" role="menuitem" @click="addNodeFromContext('video')"><Video :size="15" /> 视频节点</button>
            <button type="button" role="menuitem" @click="addNodeFromContext('text')"><FileText :size="15" /> 文本节点</button>
          </template>
        </div>

        <div v-if="!nodes.length" class="canvas-empty-state">
          <div class="canvas-empty-icon"><Sparkles :size="24" /></div>
          <h1>从一个想法开始</h1>
          <p>添加图片、视频或文本节点，组合成自己的创作流程。</p>
          <button class="primary-button" type="button" @click="addNode('image')"><ImageIcon :size="18" /> 添加图片节点</button>
        </div>

        <CanvasToolbar
          :zoom="viewport.zoom"
          :selection-count="selectionCount"
          :can-locate="Boolean(selectedId)"
          :can-copy="hasSelection"
          :can-paste="Boolean(canvasClipboard?.nodes.length)"
          @zoom-out="changeZoom(-0.1)"
          @reset-viewport="resetViewport()"
          @zoom-in="changeZoom(0.1)"
          @fit-all="fitAllNodes"
          @locate-selected="locateSelectedNode"
          @copy="copySelectedNodes"
          @paste="pasteCanvasClipboard"
        />

        <CanvasMinimap
          :nodes="minimapNodes"
          :viewport="viewport"
          :surface-width="surfaceWidth"
          :surface-height="surfaceHeight"
          :selected-ids="selectedIdList"
          :compact="Boolean(compactViewport?.matches)"
          @navigate="navigateToWorld($event.x, $event.y)"
        />

        <p v-if="connectionSourceId" class="connection-hint"><GitBranch :size="16" /> {{ connectionHint }}</p>
      </section>

    </div>

    <CanvasWorkflowPanel
      :open="workflowPanelOpen"
      :loading="loadingWorkflows"
      :workflows="workflows"
      :active-workflow-id="activeWorkflowId"
      :saving="savingWorkflow"
      :deleting="deletingWorkflows"
      :dirty="workflowDirty"
      :last-saved-at="workflowLastSavedAt"
      @close="workflowPanelOpen = false"
      @refresh="loadWorkflows"
      @open-workflow="openWorkflow"
      @delete-workflow="deleteWorkflow"
      @delete-workflows="deleteWorkflowBatch"
      @save="saveWorkflow()"
    />

    <CanvasAssistantDock
      v-model:open="assistantOpen"
      v-model:input="chatInput"
      :chat-enabled="capabilities.chat"
      :messages="chatMessages"
      :sending="chatSending"
      @new-chat="newChat"
      @send="sendChat"
      @toggle="toggleAssistant"
    />

    <Transition name="toast">
      <p v-if="toast" class="toast-message">{{ toast }}</p>
    </Transition>
    <StoryboardDialog
      :open="storyboardOpen"
      :workflow-id="activeWorkflowId"
      @close="storyboardOpen = false"
      @add-prompt="addStoryboardPrompt"
      @error="showToast"
    />
    <VideoTimelineWorkbench
      v-model="timeline"
      v-model:latest-composition="latestComposition"
      :open="timelineOpen"
      :workflow-id="activeWorkflowId"
      :workflow-title="workflowTitle"
      :nodes="nodes"
      :connections="connections"
      :saving="savingWorkflow"
      @close="timelineOpen = false"
      @save="saveTimeline"
      @notify="showToast"
    />
  </main>
</template>
