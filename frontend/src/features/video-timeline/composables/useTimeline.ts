import { computed, ref, toRaw, type Ref } from 'vue';

import type { CanvasConnection, CanvasNode } from '@/features/infinite-canvas/types';
import type {
  TimelineAudioClip,
  TimelineDocument,
  TimelineOutputSettings,
  TimelineSubtitleCue,
  TimelineVideoClip,
} from '@/features/video-timeline/types/timeline';

const DEFAULT_OUTPUT: TimelineOutputSettings = {
  aspectRatio: '16:9',
  resolution: '1080p',
  fps: 25,
  burnSubtitles: true,
  fit: 'contain',
  backgroundColor: '#05070c',
};

const DEFAULT_VISUAL_KEYFRAME = {
  scale: 1,
  x: 0,
  y: 0,
  rotation: 0,
  brightness: 0,
  contrast: 1,
  saturation: 1,
};

export interface TimelineVideoSource {
  sourceNodeId?: string;
  title?: string;
  sourceUrl: string;
  thumbnailUrl?: string;
  duration?: number;
  sourceDuration?: number;
}

function normalizeVisualKeyframe(value?: Partial<typeof DEFAULT_VISUAL_KEYFRAME>) {
  const source = { ...DEFAULT_VISUAL_KEYFRAME, ...(value || {}) };
  return {
    scale: Math.min(3, Math.max(1, Number(source.scale) || 1)),
    x: Math.min(1, Math.max(-1, Number(source.x) || 0)),
    y: Math.min(1, Math.max(-1, Number(source.y) || 0)),
    rotation: Math.min(180, Math.max(-180, Number(source.rotation) || 0)),
    brightness: Math.min(0.5, Math.max(-0.5, Number(source.brightness) || 0)),
    contrast: Math.min(2, Math.max(0.5, Number(source.contrast) || 1)),
    saturation: Math.min(3, Math.max(0, Number(source.saturation) || 0)),
  };
}

export function makeTimelineId(prefix: string) {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
    : Math.random().toString(36).slice(2, 12);
  return `${prefix}_${Date.now().toString(36)}_${random}`;
}

export function createTimelineVideoClip(
  source: TimelineVideoSource,
  track: TimelineVideoClip['track'] = 0,
  timelineStart = 0,
): TimelineVideoClip {
  const sourceDuration = Number(source.sourceDuration ?? source.duration);
  const hasSourceDuration = Number.isFinite(sourceDuration) && sourceDuration > 0;
  const duration = Math.max(
    0.1,
    Math.min(600, Number(source.duration) || (hasSourceDuration ? sourceDuration : 5)),
  );
  return {
    id: makeTimelineId('clip'),
    sourceNodeId: source.sourceNodeId,
    title: source.title || '视频片段',
    sourceUrl: source.sourceUrl,
    thumbnailUrl: source.thumbnailUrl || source.sourceUrl,
    sourceStart: 0,
    sourceDuration: hasSourceDuration ? sourceDuration : undefined,
    duration: hasSourceDuration ? Math.min(duration, Math.max(0.1, sourceDuration)) : duration,
    track,
    timelineStart: Math.max(0, Number(timelineStart) || 0),
    opacity: 1,
    volume: 1,
    muted: false,
    transition: { type: 'cut', duration: 0.35 },
    keyframes: { start: { ...DEFAULT_VISUAL_KEYFRAME }, end: { ...DEFAULT_VISUAL_KEYFRAME } },
  };
}

export function cloneTimeline(value?: TimelineDocument): TimelineDocument | undefined {
  if (!value) return undefined;
  return JSON.parse(JSON.stringify(toRaw(value))) as TimelineDocument;
}

function sortVideoNodes(nodes: CanvasNode[], connections: CanvasConnection[]) {
  const videos = nodes.filter((node) => node.type === 'video' && Boolean(node.resultUrl || node.inputUrl));
  const ids = new Set(videos.map((node) => node.id));
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, number>();
  for (const node of videos) {
    outgoing.set(node.id, []);
    incoming.set(node.id, 0);
  }
  for (const connection of connections) {
    if (!ids.has(connection.from) || !ids.has(connection.to)) continue;
    outgoing.get(connection.from)?.push(connection.to);
    incoming.set(connection.to, (incoming.get(connection.to) || 0) + 1);
  }
  const compare = (left: CanvasNode, right: CanvasNode) => left.y - right.y || left.x - right.x;
  const byId = new Map(videos.map((node) => [node.id, node]));
  const ready = videos.filter((node) => (incoming.get(node.id) || 0) === 0).sort(compare);
  const ordered: CanvasNode[] = [];
  const visited = new Set<string>();
  while (ready.length) {
    const node = ready.shift();
    if (!node || visited.has(node.id)) continue;
    visited.add(node.id);
    ordered.push(node);
    const next = (outgoing.get(node.id) || [])
      .map((id) => byId.get(id))
      .filter((item): item is CanvasNode => Boolean(item))
      .sort(compare);
    for (const child of next) {
      const count = (incoming.get(child.id) || 1) - 1;
      incoming.set(child.id, count);
      if (count <= 0) ready.push(child);
    }
    ready.sort(compare);
  }
  return ordered.concat(videos.filter((node) => !visited.has(node.id)).sort(compare));
}

export function timelineFromCanvas(nodes: CanvasNode[], connections: CanvasConnection[]): TimelineDocument {
  const videoClips: TimelineVideoClip[] = sortVideoNodes(nodes, connections).map((node) => createTimelineVideoClip({
    sourceNodeId: node.id,
    title: node.title,
    sourceUrl: node.resultUrl || node.inputUrl || '',
    duration: Number(node.sourceDuration ?? node.duration) || undefined,
    sourceDuration: Number(node.sourceDuration ?? node.duration) || undefined,
  }));
  return {
    version: 1,
    videoClips,
    audioClips: [],
    subtitles: [],
    output: { ...DEFAULT_OUTPUT },
  };
}

export function normalizeTimeline(value: TimelineDocument | undefined, nodes: CanvasNode[], connections: CanvasConnection[]): TimelineDocument {
  const fallback = timelineFromCanvas(nodes, connections);
  if (!value) return fallback;
  const sourceByNode = new Map(nodes.map((node) => [node.id, node]));
  const videoClips: TimelineVideoClip[] = (value.videoClips || []).filter((clip) => clip && clip.sourceUrl).map((clip) => ({
    ...clip,
    sourceStart: Math.max(0, Number(clip.sourceStart) || 0),
    sourceDuration: Number(clip.sourceDuration) > 0 ? Number(clip.sourceDuration) : undefined,
    duration: Math.max(0.1, Number(clip.duration) || 5),
    track: clip.track === 1 || clip.track === 2 ? clip.track : 0,
    timelineStart: Math.max(0, Number(clip.timelineStart) || 0),
    opacity: Math.min(1, Math.max(0, Number(clip.opacity ?? 1))),
    volume: Math.min(2, Math.max(0, Number(clip.volume ?? 1))),
    muted: Boolean(clip.muted),
    transition: {
      type: clip.transition?.type === 'fade' ? 'fade' : 'cut',
      duration: Math.max(0.05, Number(clip.transition?.duration) || 0.35),
    },
    keyframes: {
      start: normalizeVisualKeyframe(clip.keyframes?.start),
      end: normalizeVisualKeyframe(clip.keyframes?.end),
    },
    thumbnailUrl: clip.thumbnailUrl
      || sourceByNode.get(clip.sourceNodeId || '')?.resultUrl
      || sourceByNode.get(clip.sourceNodeId || '')?.inputUrl,
  }));
  const existingSources = new Set(videoClips.map((clip) => clip.sourceNodeId).filter(Boolean));
  for (const clip of fallback.videoClips) {
    if (!clip.sourceNodeId || existingSources.has(clip.sourceNodeId)) continue;
    videoClips.push(clip);
  }
  return {
    version: 1 as const,
    videoClips,
    audioClips: (value.audioClips || []).map((clip) => ({
      ...clip,
      start: Math.max(0, Number(clip.start) || 0),
      sourceStart: Math.max(0, Number(clip.sourceStart) || 0),
      duration: Math.max(0.1, Number(clip.duration) || 5),
      volume: Math.min(2, Math.max(0, Number(clip.volume) || 0)),
      fadeIn: Math.max(0, Number(clip.fadeIn) || 0),
      fadeOut: Math.max(0, Number(clip.fadeOut) || 0),
    })),
    subtitles: (value.subtitles || []).map((cue) => ({
      ...cue,
      start: Math.max(0, Number(cue.start) || 0),
      end: Math.max(0.1, Number(cue.end) || 1),
      text: String(cue.text || '').trim(),
    })).filter((cue) => cue.text && cue.end > cue.start),
    output: { ...DEFAULT_OUTPUT, ...(value.output || {}) },
  };
}

export function timelineDuration(timeline?: TimelineDocument) {
  if (!timeline) return 0;
  const primary = timeline.videoClips.filter((clip) => clip.track === 0);
  return primary.reduce((total, clip, index) => {
    const overlap = index > 0 && clip.transition.type === 'fade'
      ? Math.min(clip.transition.duration, clip.duration / 2, primary[index - 1].duration / 2)
      : 0;
    return total + clip.duration - overlap;
  }, 0);
}

export function formatTimelineTime(value: number) {
  const seconds = Math.max(0, Math.round(value * 10) / 10);
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds - minutes * 60).toFixed(1).padStart(4, '0');
  return `${String(minutes).padStart(2, '0')}:${remainder}`;
}

export function formatTimelineBytes(value?: number) {
  if (!value || value < 1) return '';
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function useTimeline(
  initialTimeline: Readonly<Ref<TimelineDocument | undefined>>,
  nodes: Readonly<Ref<CanvasNode[]>>,
  connections: Readonly<Ref<CanvasConnection[]>>,
) {
  const timeline = ref<TimelineDocument>(normalizeTimeline(initialTimeline.value, nodes.value, connections.value));
  const selectedClipId = ref<string>();
  const isDirty = ref(false);
  const canUndo = ref(false);
  const canRedo = ref(false);
  const undoStack: TimelineDocument[] = [];
  const redoStack: TimelineDocument[] = [];
  let currentSnapshot = cloneTimeline(timeline.value) as TimelineDocument;
  const duration = computed(() => timelineDuration(timeline.value));
  const selectedClip = computed(() => timeline.value.videoClips.find((clip) => clip.id === selectedClipId.value));

  function replace(value: TimelineDocument) {
    timeline.value = normalizeTimeline(value, nodes.value, connections.value);
    isDirty.value = false;
    undoStack.length = 0;
    redoStack.length = 0;
    currentSnapshot = cloneTimeline(timeline.value) as TimelineDocument;
    canUndo.value = false;
    canRedo.value = false;
    selectedClipId.value ||= timeline.value.videoClips[0]?.id;
  }

  function markDirty() {
    const next = cloneTimeline(timeline.value) as TimelineDocument;
    if (JSON.stringify(next) !== JSON.stringify(currentSnapshot)) {
      undoStack.push(currentSnapshot);
      if (undoStack.length > 80) undoStack.shift();
      currentSnapshot = next;
      redoStack.length = 0;
      canUndo.value = undoStack.length > 0;
      canRedo.value = false;
    }
    isDirty.value = true;
  }

  function markSaved() {
    isDirty.value = false;
  }

  function undo() {
    const previous = undoStack.pop();
    if (!previous) return false;
    redoStack.push(cloneTimeline(timeline.value) as TimelineDocument);
    timeline.value = cloneTimeline(previous) as TimelineDocument;
    currentSnapshot = cloneTimeline(previous) as TimelineDocument;
    canUndo.value = undoStack.length > 0;
    canRedo.value = true;
    isDirty.value = true;
    return true;
  }

  function redo() {
    const next = redoStack.pop();
    if (!next) return false;
    undoStack.push(cloneTimeline(timeline.value) as TimelineDocument);
    timeline.value = cloneTimeline(next) as TimelineDocument;
    currentSnapshot = cloneTimeline(next) as TimelineDocument;
    canUndo.value = true;
    canRedo.value = redoStack.length > 0;
    isDirty.value = true;
    return true;
  }

  function selectClip(id?: string) {
    selectedClipId.value = id;
  }

  function updateVideoClip(id: string, updates: Partial<TimelineVideoClip>) {
    const clip = timeline.value.videoClips.find((item) => item.id === id);
    if (!clip) return;
    Object.assign(clip, updates);
    clip.sourceStart = Math.max(0, Number(clip.sourceStart) || 0);
    if (clip.sourceDuration) {
      clip.sourceStart = Math.min(clip.sourceStart, Math.max(0, clip.sourceDuration - 0.1));
      clip.duration = Math.min(clip.duration, Math.max(0.1, clip.sourceDuration - clip.sourceStart));
    }
    clip.duration = Math.max(0.1, Number(clip.duration) || 0.1);
    clip.track = clip.track === 1 || clip.track === 2 ? clip.track : 0;
    clip.timelineStart = Math.max(0, Number(clip.timelineStart) || 0);
    clip.opacity = Math.min(1, Math.max(0, Number(clip.opacity ?? 1)));
    clip.volume = Math.min(2, Math.max(0, Number(clip.volume) || 0));
    markDirty();
  }

  function updateAudioClip(id: string, updates: Partial<TimelineAudioClip>) {
    const clip = timeline.value.audioClips.find((item) => item.id === id);
    if (!clip) return;
    Object.assign(clip, updates);
    clip.start = Math.max(0, Number(clip.start) || 0);
    clip.sourceStart = Math.max(0, Number(clip.sourceStart) || 0);
    clip.duration = Math.max(0.1, Number(clip.duration) || 0.1);
    clip.volume = Math.min(2, Math.max(0, Number(clip.volume) || 0));
    clip.fadeIn = Math.min(clip.duration, Math.max(0, Number(clip.fadeIn) || 0));
    clip.fadeOut = Math.min(clip.duration, Math.max(0, Number(clip.fadeOut) || 0));
    markDirty();
  }

  function updateSubtitle(id: string, updates: Partial<TimelineSubtitleCue>) {
    const cue = timeline.value.subtitles.find((item) => item.id === id);
    if (!cue) return;
    Object.assign(cue, updates);
    cue.start = Math.max(0, Number(cue.start) || 0);
    cue.end = Math.max(cue.start + 0.1, Number(cue.end) || cue.start + 0.1);
    markDirty();
  }

  return {
    timeline,
    selectedClipId,
    selectedClip,
    duration,
    isDirty,
    canUndo,
    canRedo,
    replace,
    markDirty,
    markSaved,
    undo,
    redo,
    selectClip,
    updateVideoClip,
    updateAudioClip,
    updateSubtitle,
  };
}
