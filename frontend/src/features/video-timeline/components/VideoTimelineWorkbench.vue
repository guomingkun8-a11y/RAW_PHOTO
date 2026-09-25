<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch, type CSSProperties } from 'vue';
import {
  ArrowLeft,
  Captions,
  ChevronLeft,
  ChevronRight,
  Download,
  Film,
  History,
  Layers,
  LoaderCircle,
  Mic2,
  Music2,
  Pause,
  Play,
  Plus,
  Redo2,
  Save,
  Scissors,
  Settings2,
  Sparkles,
  Undo2,
  Upload,
  Video,
  WandSparkles,
  ZoomIn,
  ZoomOut,
} from '@lucide/vue';

import type { CanvasConnection, CanvasNode } from '@/features/infinite-canvas/types';
import TimelineClip from '@/features/video-timeline/components/TimelineClip.vue';
import TimelineInspector from '@/features/video-timeline/components/TimelineInspector.vue';
import TimelineRuler from '@/features/video-timeline/components/TimelineRuler.vue';
import TimelineTrack from '@/features/video-timeline/components/TimelineTrack.vue';
import TimelineTaskPanel from '@/features/video-timeline/components/TimelineTaskPanel.vue';
import TimelineVoiceoverPanel from '@/features/video-timeline/components/TimelineVoiceoverPanel.vue';
import {
  cloneTimeline,
  createTimelineVideoClip,
  formatTimelineBytes,
  formatTimelineTime,
  makeTimelineId,
  normalizeTimeline,
  useTimeline,
} from '@/features/video-timeline/composables/useTimeline';
import {
  createCompositionTask,
  cancelCompositionTask,
  deleteCompositionTask,
  downloadComposition,
  generateTimelineVoiceover,
  getTimelineCapabilities,
  listCompositionTasks,
  pollCompositionTask,
  retryCompositionTask,
  transcribeTimelineAudio,
  uploadTimelineAudio,
  uploadTimelineVideo,
} from '@/features/video-timeline/services/timeline-api';
import type {
  CompositionTask,
  TimelineAudioClip,
  TimelineCapabilities,
  TimelineDocument,
  TimelineOutputSettings,
  TimelineSubtitleCue,
  TimelineVideoClip,
  TimelineVisualKeyframe,
  TimelineVoiceoverInput,
} from '@/features/video-timeline/types/timeline';
import { captionsFromText, parseSrt } from '@/features/video-timeline/utils/subtitles';
import '@/features/video-timeline/assets/timeline.css';

const props = defineProps<{
  open: boolean;
  workflowId: string;
  workflowTitle: string;
  modelValue?: TimelineDocument;
  nodes: CanvasNode[];
  connections: CanvasConnection[];
  latestComposition?: CompositionTask;
  saving?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  save: [timeline: TimelineDocument, latestComposition?: CompositionTask];
  'update:modelValue': [timeline: TimelineDocument];
  'update:latestComposition': [task: CompositionTask | undefined];
  notify: [message: string];
}>();

const sourceTimeline = computed(() => props.modelValue);
const sourceNodes = computed(() => props.nodes);
const sourceConnections = computed(() => props.connections);
const {
  timeline,
  duration,
  isDirty,
  canUndo,
  canRedo,
  replace,
  markDirty,
  markSaved,
  undo,
  redo,
  updateVideoClip,
  updateAudioClip,
  updateSubtitle,
} = useTimeline(sourceTimeline, sourceNodes, sourceConnections);

type SelectionKind = 'video' | 'audio' | 'subtitle' | 'output';

const selectionKind = ref<SelectionKind>('output');
const selectedId = ref<string>();
const draggingClipId = ref<string>();
const dragTargetTrack = ref<TimelineVideoClip['track']>();
const pixelsPerSecond = ref(34);
const voiceoverInput = ref<HTMLInputElement>();
const musicInput = ref<HTMLInputElement>();
const subtitleInput = ref<HTMLInputElement>();
const videoInput = ref<HTMLInputElement>();
const pendingVideoTrack = ref<TimelineVideoClip['track']>(0);
const uploadingKind = ref<'video' | 'voiceover' | 'music'>();
const compositionTask = ref<CompositionTask>();
const exporting = ref(false);
const downloading = ref(false);
const previewMode = ref<'clip' | 'result'>('clip');
const previewVideo = ref<HTMLVideoElement>();
const playhead = ref(0);
const timelinePlaying = ref(false);
const aiPanelOpen = ref(false);
const capabilities = ref<TimelineCapabilities>();
const loadingCapabilities = ref(false);
const generatingVoiceover = ref(false);
const transcribingId = ref<string>();
const taskPanelOpen = ref(false);
const compositionTasks = ref<CompositionTask[]>([]);
const loadingTasks = ref(false);
const deletingTaskId = ref<string>();
const audioElements = new Map<string, HTMLAudioElement>();
const overlayVideoElements = new Map<string, HTMLVideoElement>();
type TimedDrag = { kind: 'audio' | 'subtitle'; id: string; startX: number; initialStart: number; moved: boolean };
let timedDrag: TimedDrag | undefined;
let compositionController: AbortController | undefined;
let playbackFrame = 0;

const selectedVideoClip = computed(() => selectionKind.value === 'video'
  ? timeline.value.videoClips.find((clip) => clip.id === selectedId.value)
  : undefined);
const selectedAudioClip = computed(() => selectionKind.value === 'audio'
  ? timeline.value.audioClips.find((clip) => clip.id === selectedId.value)
  : undefined);
const selectedSubtitle = computed(() => selectionKind.value === 'subtitle'
  ? timeline.value.subtitles.find((cue) => cue.id === selectedId.value)
  : undefined);
const resultUrl = computed(() => compositionTask.value?.result_url || compositionTask.value?.resultUrl || '');
const timelineWidth = computed(() => Math.max(760, Math.ceil((duration.value + 2) * pixelsPerSecond.value)));

type VideoTrack = TimelineVideoClip['track'];
type VideoPlacement = {
  clip: TimelineVideoClip;
  start: number;
  end: number;
  left: number;
  width: number;
  index: number;
};

function placePrimaryClips(clips: TimelineVideoClip[]): VideoPlacement[] {
  let start = 0;
  return clips.map((clip, index) => {
    const overlap = index > 0 && clip.transition.type === 'fade'
      ? Math.min(clip.transition.duration, clip.duration / 2, (clips[index - 1]?.duration || clip.duration) / 2)
      : 0;
    start -= overlap;
    const placement = {
      clip,
      start,
      end: start + clip.duration,
      left: start * pixelsPerSecond.value,
      width: Math.max(82, clip.duration * pixelsPerSecond.value),
      index,
    };
    start += clip.duration;
    return placement;
  });
}

const primaryVideoClips = computed(() => timeline.value.videoClips.filter((clip) => clip.track === 0));
const videoPlacementsByTrack = computed<Record<VideoTrack, VideoPlacement[]>>(() => ({
  0: placePrimaryClips(primaryVideoClips.value),
  1: timeline.value.videoClips
    .filter((clip) => clip.track === 1)
    .sort((left, right) => left.timelineStart - right.timelineStart)
    .map((clip, index) => ({
      clip,
      start: clip.timelineStart,
      end: clip.timelineStart + clip.duration,
      left: clip.timelineStart * pixelsPerSecond.value,
      width: Math.max(82, clip.duration * pixelsPerSecond.value),
      index,
    })),
  2: timeline.value.videoClips
    .filter((clip) => clip.track === 2)
    .sort((left, right) => left.timelineStart - right.timelineStart)
    .map((clip, index) => ({
      clip,
      start: clip.timelineStart,
      end: clip.timelineStart + clip.duration,
      left: clip.timelineStart * pixelsPerSecond.value,
      width: Math.max(82, clip.duration * pixelsPerSecond.value),
      index,
    })),
}));
const primaryVideoPlacements = computed(() => videoPlacementsByTrack.value[0]);
const overlayVideoPlacements = computed(() => [
  ...videoPlacementsByTrack.value[1],
  ...videoPlacementsByTrack.value[2],
]);
const videoPlacements = computed(() => [
  ...primaryVideoPlacements.value,
  ...overlayVideoPlacements.value,
]);
const activeOverlayPlacements = computed(() => overlayVideoPlacements.value.filter((placement) => (
  playhead.value >= placement.start
  && playhead.value < Math.min(duration.value, placement.end)
  && placement.clip.opacity > 0
)));
const previewClip = computed(() => placementAt(playhead.value)?.clip || primaryVideoClips.value[0]);

function visualStyleFor(clip: TimelineVideoClip, placement?: VideoPlacement, opacity = 1): CSSProperties {
  const progress = placement ? Math.min(1, Math.max(0, (playhead.value - placement.start) / clip.duration)) : 0;
  const interpolate = (start: number, end: number) => start + (end - start) * progress;
  const start = clip.keyframes.start;
  const end = clip.keyframes.end;
  const scale = interpolate(start.scale, end.scale);
  const x = interpolate(start.x, end.x) * 25;
  const y = interpolate(start.y, end.y) * 25;
  const rotation = interpolate(start.rotation, end.rotation);
  const brightness = Math.max(0.5, 1 + interpolate(start.brightness, end.brightness));
  const contrast = interpolate(start.contrast, end.contrast);
  const saturation = interpolate(start.saturation, end.saturation);
  return {
    objectFit: timeline.value.output.fit,
    transform: `translate(${x}%, ${y}%) scale(${scale}) rotate(${rotation}deg)`,
    filter: `brightness(${brightness}) contrast(${contrast}) saturate(${saturation})`,
    opacity,
  };
}

const previewVisualStyle = computed(() => {
  const clip = previewClip.value;
  if (!clip) return {};
  return visualStyleFor(clip, videoPlacements.value.find((item) => item.clip.id === clip.id));
});

function overlayPreviewStyle(placement: VideoPlacement): CSSProperties {
  const active = activeOverlayPlacements.value.some((item) => item.clip.id === placement.clip.id);
  return {
    ...visualStyleFor(placement.clip, placement, active ? placement.clip.opacity : 0),
    visibility: active ? 'visible' : 'hidden',
    zIndex: placement.clip.track + 1,
  };
}

watch(() => props.open, (open) => {
  if (!open) return;
  replace(normalizeTimeline(props.modelValue, props.nodes, props.connections));
  compositionTask.value = props.latestComposition;
  const first = timeline.value.videoClips[0];
  selectionKind.value = first ? 'video' : 'output';
  selectedId.value = first?.id;
  previewMode.value = props.latestComposition?.status === 'success' ? 'result' : 'clip';
  playhead.value = 0;
  timelinePlaying.value = false;
  aiPanelOpen.value = false;
  taskPanelOpen.value = false;
  void loadCapabilities();
}, { immediate: true });

watch(() => props.latestComposition, (task) => {
  if (task) compositionTask.value = task;
});

function messageFrom(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。';
}

function placementAt(value: number) {
  const placements = primaryVideoPlacements.value;
  if (!placements.length) return undefined;
  for (let index = placements.length - 1; index >= 0; index -= 1) {
    const placement = placements[index];
    if (placement && value >= placement.start && value < placement.end) return placement;
  }
  return value >= duration.value ? placements.at(-1) : placements[0];
}

async function loadCapabilities() {
  if (loadingCapabilities.value || capabilities.value) return;
  loadingCapabilities.value = true;
  try {
    capabilities.value = await getTimelineCapabilities();
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    loadingCapabilities.value = false;
  }
}

async function loadTasks() {
  if (loadingTasks.value) return;
  loadingTasks.value = true;
  try {
    const result = await listCompositionTasks(props.workflowId);
    compositionTasks.value = result.items;
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    loadingTasks.value = false;
  }
}

function openTaskPanel() {
  taskPanelOpen.value = true;
  void loadTasks();
}

function select(kind: SelectionKind, id?: string) {
  selectionKind.value = kind;
  selectedId.value = id;
  if (kind === 'video') {
    previewMode.value = 'clip';
    const placement = videoPlacements.value.find((item) => item.clip.id === id);
    if (placement) {
      playhead.value = Math.min(duration.value, placement.start);
      timelinePlaying.value = false;
      previewVideo.value?.pause();
      pauseTimelineAudio();
      pauseOverlayVideos();
      void syncPreviewToPlayhead(false, false);
    }
  }
}

function persist() {
  const value = cloneTimeline(timeline.value) || timeline.value;
  emit('update:modelValue', value);
  emit('save', value, compositionTask.value);
  markSaved();
}

function closeWorkbench() {
  if (isDirty.value && !window.confirm('时间线还有未保存的修改，确定关闭吗？')) return;
  emit('close');
}

function videoTrackGroups(excludeId?: string): Record<VideoTrack, TimelineVideoClip[]> {
  return {
    0: timeline.value.videoClips.filter((clip) => clip.track === 0 && clip.id !== excludeId),
    1: timeline.value.videoClips.filter((clip) => clip.track === 1 && clip.id !== excludeId),
    2: timeline.value.videoClips.filter((clip) => clip.track === 2 && clip.id !== excludeId),
  };
}

function applyVideoTrackGroups(groups: Record<VideoTrack, TimelineVideoClip[]>) {
  timeline.value.videoClips = [...groups[0], ...groups[1], ...groups[2]];
}

function dropTimeFor(event: DragEvent) {
  const lane = event.currentTarget as HTMLElement;
  const rect = lane.getBoundingClientRect();
  const rawTime = Math.max(0, (event.clientX - rect.left) / pixelsPerSecond.value);
  return event.altKey ? rawTime : Math.round(rawTime * 10) / 10;
}

function insertVideoClip(clip: TimelineVideoClip, track: VideoTrack, dropTime?: number) {
  const groups = videoTrackGroups();
  clip.track = track;
  if (track === 0) {
    clip.timelineStart = 0;
    const placements = placePrimaryClips(groups[0]);
    const insertion = dropTime === undefined
      ? groups[0].length
      : placements.findIndex((placement) => dropTime < placement.start + placement.clip.duration / 2);
    groups[0].splice(insertion < 0 ? groups[0].length : insertion, 0, clip);
  } else {
    clip.timelineStart = Math.min(Math.max(0, duration.value - 0.1), Math.max(0, dropTime ?? playhead.value));
    groups[track].push(clip);
    groups[track].sort((left, right) => left.timelineStart - right.timelineStart);
  }
  applyVideoTrackGroups(groups);
  markDirty();
  select('video', clip.id);
}

function isSupportedVideo(file: File) {
  return file.type.startsWith('video/') || /\.(mp4|webm|mov|m4v|avi|mkv)$/i.test(file.name);
}

function readLocalVideoDuration(file: File): Promise<number | undefined> {
  return new Promise((resolve) => {
    const source = URL.createObjectURL(file);
    const element = document.createElement('video');
    let settled = false;
    const finish = (value?: number) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      element.removeAttribute('src');
      element.load();
      URL.revokeObjectURL(source);
      resolve(value);
    };
    const timer = window.setTimeout(() => finish(), 8_000);
    element.preload = 'metadata';
    element.muted = true;
    element.onloadedmetadata = () => finish(
      Number.isFinite(element.duration) && element.duration > 0
        ? Math.round(element.duration * 1000) / 1000
        : undefined,
    );
    element.onerror = () => finish();
    element.src = source;
  });
}

function chooseVideoUpload(track: VideoTrack) {
  if (uploadingKind.value) return;
  if (track > 0 && !primaryVideoClips.value.length) {
    emit('notify', '请先向主视频轨添加一个视频。');
    return;
  }
  pendingVideoTrack.value = track;
  videoInput.value?.click();
}

async function uploadVideoFile(file: File, track: VideoTrack, dropTime?: number) {
  if (uploadingKind.value) {
    emit('notify', '当前有素材正在上传，请稍候。');
    return;
  }
  if (track > 0 && !primaryVideoClips.value.length) {
    emit('notify', '请先向主视频轨添加一个视频。');
    return;
  }
  if (!isSupportedVideo(file)) {
    emit('notify', '请选择 MP4、WEBM、MOV、AVI 或 MKV 视频。');
    return;
  }
  if (file.size > 300 * 1024 * 1024) {
    emit('notify', '视频不能超过 300 MB。');
    return;
  }

  pendingVideoTrack.value = track;
  uploadingKind.value = 'video';
  emit('notify', `正在上传 ${file.name}...`);
  try {
    const [uploaded, sourceDuration] = await Promise.all([
      uploadTimelineVideo(file, props.workflowId),
      readLocalVideoDuration(file),
    ]);
    const title = (uploaded.name || uploaded.filename || file.name).replace(/\.[^.]+$/, '') || '上传视频';
    const clip = createTimelineVideoClip({
      title,
      sourceUrl: uploaded.url,
      duration: sourceDuration,
      sourceDuration,
    }, track, dropTime);
    insertVideoClip(clip, track, dropTime);
    await nextTick();
    void syncPreviewToPlayhead(false, false);
    emit('notify', `${file.name} 已加入${track === 0 ? '主视频轨' : `叠加轨 ${track}`}。`);
  } catch (error) {
    emit('notify', `视频上传失败：${messageFrom(error)}`);
  } finally {
    uploadingKind.value = undefined;
  }
}

function handleVideoUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (file) void uploadVideoFile(file, pendingVideoTrack.value);
}

function moveSelected(direction: -1 | 1) {
  const clip = selectedVideoClip.value;
  if (!clip) return;
  if (clip.track > 0) {
    updateSelectedVideo({ timelineStart: clip.timelineStart + direction * 0.1 });
    return;
  }
  const groups = videoTrackGroups();
  const trackClips = groups[clip.track];
  const from = trackClips.findIndex((item) => item.id === clip.id);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= trackClips.length) return;
  const [moved] = trackClips.splice(from, 1);
  if (!moved) return;
  trackClips.splice(to, 0, moved);
  applyVideoTrackGroups(groups);
  markDirty();
}

function updateSelectedVideo(updates: Partial<TimelineVideoClip>) {
  const clip = selectedVideoClip.value;
  if (!clip) return;
  const nextTrack = updates.track;
  if (nextTrack !== undefined && nextTrack !== clip.track) {
    if (clip.track === 0 && nextTrack > 0 && primaryVideoClips.value.length <= 1) {
      emit('notify', '主视频轨至少需要保留一个片段。');
      return;
    }
    const placement = videoPlacements.value.find((item) => item.clip.id === clip.id);
    const groups = videoTrackGroups(clip.id);
    const previousTrack = clip.track;
    clip.track = nextTrack;
    clip.timelineStart = nextTrack === 0
      ? 0
      : Math.min(Math.max(0, duration.value - 0.1), previousTrack > 0 ? clip.timelineStart : placement?.start || 0);
    groups[nextTrack].push(clip);
    if (nextTrack > 0) groups[nextTrack].sort((left, right) => left.timelineStart - right.timelineStart);
    applyVideoTrackGroups(groups);
    markDirty();
    void syncPreviewToPlayhead(false, false);
    return;
  }
  const normalized = { ...updates };
  if (normalized.timelineStart !== undefined) {
    normalized.timelineStart = Math.min(
      Math.max(0, duration.value - 0.1),
      Math.max(0, Number(normalized.timelineStart) || 0),
    );
  }
  updateVideoClip(clip.id, normalized);
  void syncPreviewToPlayhead(false, false);
}

function undoEdit() {
  if (!undo()) return;
  timelinePlaying.value = false;
  previewVideo.value?.pause();
  pauseOverlayVideos();
  if (selectedId.value && ![
    ...timeline.value.videoClips,
    ...timeline.value.audioClips,
    ...timeline.value.subtitles,
  ].some((item) => item.id === selectedId.value)) select('output');
  playhead.value = Math.min(playhead.value, duration.value);
}

function redoEdit() {
  if (!redo()) return;
  timelinePlaying.value = false;
  previewVideo.value?.pause();
  pauseOverlayVideos();
  playhead.value = Math.min(playhead.value, duration.value);
}

async function syncPreviewToPlayhead(autoplay = false, selectPrimary = false) {
  const placement = placementAt(playhead.value);
  if (!placement) return;
  if (selectPrimary && (selectedId.value !== placement.clip.id || selectionKind.value !== 'video')) {
    selectionKind.value = 'video';
    selectedId.value = placement.clip.id;
  }
  previewMode.value = 'clip';
  await nextTick();
  syncTimelineOverlays(true);
  const element = previewVideo.value;
  if (!element) return;
  const target = placement.clip.sourceStart + Math.max(0, playhead.value - placement.start);
  if (element.readyState > 0 && Math.abs(element.currentTime - target) > 0.08) element.currentTime = target;
  if (autoplay) {
    try {
      await element.play();
    } catch {
      timelinePlaying.value = false;
      pauseTimelineAudio();
      pauseOverlayVideos();
    }
  }
}

function toggleTimelinePlayback() {
  if (timelinePlaying.value) {
    timelinePlaying.value = false;
    window.cancelAnimationFrame(playbackFrame);
    previewVideo.value?.pause();
    pauseTimelineAudio();
    pauseOverlayVideos();
    return;
  }
  if (!primaryVideoClips.value.length) return;
  if (playhead.value >= duration.value - 0.05) playhead.value = 0;
  timelinePlaying.value = true;
  startPlaybackLoop();
  void syncPreviewToPlayhead(true);
}

function startPlaybackLoop() {
  window.cancelAnimationFrame(playbackFrame);
  const tick = () => {
    if (!timelinePlaying.value) return;
    handlePreviewTimeUpdate();
    syncTimelineAudio();
    syncTimelineOverlays();
    playbackFrame = window.requestAnimationFrame(tick);
  };
  playbackFrame = window.requestAnimationFrame(tick);
}

function handlePreviewPlay() {
  const clip = previewClip.value;
  const placement = primaryVideoPlacements.value.find((item) => item.clip.id === clip?.id);
  if (clip && placement && previewVideo.value) {
    playhead.value = Math.min(placement.end, placement.start + Math.max(0, previewVideo.value.currentTime - clip.sourceStart));
  }
  timelinePlaying.value = true;
  syncTimelineAudio(true);
  syncTimelineOverlays(true);
  startPlaybackLoop();
}

function handlePreviewPause(event?: Event) {
  if (event && event.currentTarget !== previewVideo.value) return;
  window.cancelAnimationFrame(playbackFrame);
  timelinePlaying.value = false;
  pauseTimelineAudio();
  pauseOverlayVideos();
}

function setAudioElement(value: unknown, id: string) {
  if (value instanceof HTMLAudioElement) audioElements.set(id, value);
  else audioElements.delete(id);
}

function setOverlayVideoElement(value: unknown, id: string) {
  if (value instanceof HTMLVideoElement) overlayVideoElements.set(id, value);
  else overlayVideoElements.delete(id);
}

function pauseOverlayVideos() {
  for (const element of overlayVideoElements.values()) element.pause();
}

function syncTimelineOverlays(force = false) {
  if (previewMode.value !== 'clip') {
    pauseOverlayVideos();
    return;
  }
  for (const placement of overlayVideoPlacements.value) {
    const element = overlayVideoElements.get(placement.clip.id);
    if (!element) continue;
    const localTime = playhead.value - placement.start;
    const active = localTime >= 0 && localTime < placement.clip.duration && playhead.value < duration.value;
    if (!active) {
      element.pause();
      continue;
    }
    const desiredTime = placement.clip.sourceStart + localTime;
    if (element.readyState > 0 && (force || Math.abs(element.currentTime - desiredTime) > 0.35)) {
      element.currentTime = desiredTime;
    }
    element.muted = placement.clip.muted;
    element.volume = Math.min(1, Math.max(0, placement.clip.volume));
    if (timelinePlaying.value && element.paused) void element.play().catch(() => undefined);
    if (!timelinePlaying.value && !element.paused) element.pause();
  }
}

function pauseTimelineAudio() {
  for (const element of audioElements.values()) element.pause();
}

function syncTimelineAudio(force = false) {
  if (!timelinePlaying.value || previewMode.value !== 'clip') {
    pauseTimelineAudio();
    return;
  }
  for (const clip of timeline.value.audioClips) {
    const element = audioElements.get(clip.id);
    if (!element) continue;
    const localTime = playhead.value - clip.start;
    if (localTime < 0 || localTime >= clip.duration) {
      element.pause();
      continue;
    }
    let desiredTime = clip.sourceStart + localTime;
    if (clip.loop && Number.isFinite(element.duration) && element.duration > 0) {
      desiredTime = clip.sourceStart + (localTime % Math.max(0.05, element.duration - clip.sourceStart));
    }
    if (force || Math.abs(element.currentTime - desiredTime) > 0.35) element.currentTime = desiredTime;
    const fadeInGain = clip.fadeIn > 0 ? Math.min(1, localTime / clip.fadeIn) : 1;
    const fadeOutRemaining = clip.duration - localTime;
    const fadeOutGain = clip.fadeOut > 0 ? Math.min(1, fadeOutRemaining / clip.fadeOut) : 1;
    element.volume = Math.min(1, Math.max(0, clip.volume * fadeInGain * fadeOutGain));
    if (element.paused) void element.play().catch(() => undefined);
  }
}

function seekTo(value: number, preservePlayback = false) {
  playhead.value = Math.min(duration.value, Math.max(0, value));
  if (!preservePlayback) {
    timelinePlaying.value = false;
    previewVideo.value?.pause();
    pauseTimelineAudio();
    pauseOverlayVideos();
  }
  void syncPreviewToPlayhead(preservePlayback && timelinePlaying.value);
}

function seekFromLane(event: PointerEvent) {
  const lane = event.currentTarget as HTMLElement;
  const rect = lane.getBoundingClientRect();
  seekTo((event.clientX - rect.left) / pixelsPerSecond.value);
}

function handlePreviewMetadata() {
  const element = previewVideo.value;
  const clip = previewClip.value;
  if (!element || !clip || !Number.isFinite(element.duration) || element.duration <= 0) return;
  const sourceDuration = Math.round(element.duration * 1000) / 1000;
  if (clip.sourceDuration === sourceDuration) return;
  clip.sourceDuration = sourceDuration;
  clip.sourceStart = Math.min(clip.sourceStart, Math.max(0, sourceDuration - 0.1));
  clip.duration = Math.min(clip.duration, Math.max(0.1, sourceDuration - clip.sourceStart));
  markDirty();
}

function handleOverlayMetadata(event: Event, id: string) {
  const element = event.currentTarget as HTMLVideoElement;
  const clip = timeline.value.videoClips.find((item) => item.id === id && item.track > 0);
  if (!clip || !Number.isFinite(element.duration) || element.duration <= 0) return;
  const sourceDuration = Math.round(element.duration * 1000) / 1000;
  if (clip.sourceDuration !== sourceDuration) {
    clip.sourceDuration = sourceDuration;
    clip.sourceStart = Math.min(clip.sourceStart, Math.max(0, sourceDuration - 0.1));
    clip.duration = Math.min(clip.duration, Math.max(0.1, sourceDuration - clip.sourceStart));
    markDirty();
  }
  syncTimelineOverlays(true);
}

function handlePreviewTimeUpdate() {
  if (previewMode.value !== 'clip' || !previewVideo.value || !previewClip.value) return;
  const placement = primaryVideoPlacements.value.find((item) => item.clip.id === previewClip.value?.id);
  if (!placement) return;
  const relative = Math.max(0, previewVideo.value.currentTime - placement.clip.sourceStart);
  playhead.value = Math.min(duration.value, placement.start + Math.min(relative, placement.clip.duration));
  if (!timelinePlaying.value || relative < placement.clip.duration - 0.04) return;
  const index = primaryVideoPlacements.value.findIndex((item) => item.clip.id === placement.clip.id);
  const next = primaryVideoPlacements.value[index + 1];
  if (!next) {
    timelinePlaying.value = false;
    previewVideo.value.pause();
    pauseTimelineAudio();
    pauseOverlayVideos();
    playhead.value = duration.value;
    return;
  }
  playhead.value = next.start;
  void syncPreviewToPlayhead(true);
}

function splitSelectedClip() {
  const clip = selectedVideoClip.value;
  if (!clip) return;
  const index = timeline.value.videoClips.findIndex((item) => item.id === clip.id);
  const placement = videoPlacements.value.find((item) => item.clip.id === clip.id);
  if (index < 0 || !placement) return;
  const offset = Math.round((playhead.value - placement.start) * 1000) / 1000;
  if (offset <= 0.1 || offset >= clip.duration - 0.1) {
    emit('notify', '请先把播放头移动到当前片段内部。');
    return;
  }
  const progress = offset / clip.duration;
  const midpoint = Object.fromEntries(
    Object.keys(clip.keyframes.start).map((key) => {
      const property = key as keyof TimelineVisualKeyframe;
      const start = clip.keyframes.start[property];
      const end = clip.keyframes.end[property];
      return [property, start + (end - start) * progress];
    }),
  ) as unknown as TimelineVisualKeyframe;
  const right = {
    ...JSON.parse(JSON.stringify(clip)),
    id: makeTimelineId('clip'),
    title: `${clip.title}（后半段）`,
    sourceStart: clip.sourceStart + offset,
    duration: clip.duration - offset,
    timelineStart: clip.track === 0 ? 0 : clip.timelineStart + offset,
    transition: { type: 'cut' as const, duration: 0.35 },
  };
  right.keyframes.start = { ...midpoint };
  clip.keyframes.end = { ...midpoint };
  clip.duration = offset;
  timeline.value.videoClips.splice(index + 1, 0, right);
  markDirty();
  select('video', right.id);
  playhead.value = placement.start + offset;
}

function beginClipDrag(event: DragEvent, id: string) {
  draggingClipId.value = id;
  dragTargetTrack.value = undefined;
  event.dataTransfer?.setData('text/plain', id);
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}

function indicateDropTrack(event: DragEvent, track: VideoTrack) {
  event.preventDefault();
  dragTargetTrack.value = track;
  if (event.dataTransfer) {
    const carriesFiles = Array.from(event.dataTransfer.types || []).includes('Files');
    event.dataTransfer.dropEffect = carriesFiles ? 'copy' : 'move';
  }
}

function endClipDrag() {
  draggingClipId.value = undefined;
  dragTargetTrack.value = undefined;
}

function dropClipOnTrack(event: DragEvent, track: VideoTrack) {
  event.preventDefault();
  const dropTime = dropTimeFor(event);
  const file = Array.from(event.dataTransfer?.files || []).find(isSupportedVideo);
  if (file) {
    endClipDrag();
    void uploadVideoFile(file, track, dropTime);
    return;
  }
  const sourceId = draggingClipId.value || event.dataTransfer?.getData('text/plain');
  endClipDrag();
  const clip = timeline.value.videoClips.find((item) => item.id === sourceId);
  if (!clip) return;
  if (clip.track === 0 && track > 0 && primaryVideoClips.value.length <= 1) {
    emit('notify', '主视频轨至少需要保留一个片段。');
    return;
  }

  const groups = videoTrackGroups(clip.id);
  clip.track = track;
  if (track === 0) {
    clip.timelineStart = 0;
    const placements = placePrimaryClips(groups[0]);
    const insertion = placements.findIndex((placement) => dropTime < placement.start + placement.clip.duration / 2);
    groups[0].splice(insertion < 0 ? groups[0].length : insertion, 0, clip);
  } else {
    clip.timelineStart = Math.min(Math.max(0, duration.value - 0.1), dropTime);
    groups[track].push(clip);
    groups[track].sort((left, right) => left.timelineStart - right.timelineStart);
  }
  applyVideoTrackGroups(groups);
  markDirty();
  select('video', clip.id);
}

function beginTimedDrag(event: PointerEvent, kind: 'audio' | 'subtitle', id: string) {
  if (event.button !== 0) return;
  const item = kind === 'audio'
    ? timeline.value.audioClips.find((clip) => clip.id === id)
    : timeline.value.subtitles.find((cue) => cue.id === id);
  if (!item) return;
  select(kind, id);
  timedDrag = {
    kind,
    id,
    startX: event.clientX,
    initialStart: item.start,
    moved: false,
  };
  window.addEventListener('pointermove', moveTimedDrag);
  window.addEventListener('pointerup', endTimedDrag, { once: true });
}

function moveTimedDrag(event: PointerEvent) {
  if (!timedDrag) return;
  const delta = (event.clientX - timedDrag.startX) / pixelsPerSecond.value;
  const rawStart = Math.max(0, timedDrag.initialStart + delta);
  const nextStart = event.altKey ? rawStart : Math.round(rawStart * 10) / 10;
  timedDrag.moved ||= Math.abs(delta) > 0.02;
  if (timedDrag.kind === 'audio') {
    const clip = timeline.value.audioClips.find((item) => item.id === timedDrag?.id);
    if (clip) clip.start = nextStart;
  } else {
    const cue = timeline.value.subtitles.find((item) => item.id === timedDrag?.id);
    if (cue) {
      const cueDuration = cue.end - cue.start;
      cue.start = nextStart;
      cue.end = nextStart + cueDuration;
    }
  }
}

function endTimedDrag() {
  window.removeEventListener('pointermove', moveTimedDrag);
  if (timedDrag?.moved) markDirty();
  timedDrag = undefined;
}

async function handleAudioUpload(event: Event, kind: 'voiceover' | 'music') {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file || uploadingKind.value) return;
  uploadingKind.value = kind;
  try {
    const asset = await uploadTimelineAudio(file);
    const projectDuration = Math.max(1, duration.value);
    const clip: TimelineAudioClip = {
      id: makeTimelineId(kind),
      kind,
      title: file.name.replace(/\.[^.]+$/, '') || (kind === 'voiceover' ? '旁白' : '背景音乐'),
      sourceUrl: asset.url,
      storageRel: asset.storageRel,
      start: 0,
      sourceStart: 0,
      duration: kind === 'music' ? projectDuration : Math.min(asset.duration, projectDuration),
      volume: kind === 'music' ? 0.22 : 1,
      fadeIn: kind === 'music' ? Math.min(1, asset.duration / 4) : 0,
      fadeOut: kind === 'music' ? Math.min(1.5, asset.duration / 4) : 0,
      loop: kind === 'music' && asset.duration < projectDuration,
      script: '',
      waveform: asset.waveform,
    };
    timeline.value.audioClips.push(clip);
    select('audio', clip.id);
    markDirty();
    emit('notify', kind === 'voiceover' ? '旁白音频已加入时间线。' : '背景音乐已加入时间线。');
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    uploadingKind.value = undefined;
  }
}

async function generateVoiceover(input: TimelineVoiceoverInput) {
  if (generatingVoiceover.value) return;
  generatingVoiceover.value = true;
  try {
    const asset = await generateTimelineVoiceover({
      ...input,
      workflowId: props.workflowId,
      nodeId: selectedId.value,
    });
    const start = playhead.value < duration.value ? playhead.value : 0;
    const clip: TimelineAudioClip = {
      id: makeTimelineId('voiceover'),
      kind: 'voiceover',
      title: `AI 旁白 · ${input.voice}`,
      sourceUrl: asset.url,
      storageRel: asset.storageRel,
      start,
      sourceStart: 0,
      duration: Math.min(asset.duration, Math.max(0.1, duration.value - start)),
      volume: 1,
      fadeIn: 0,
      fadeOut: 0,
      loop: false,
      script: input.text,
      waveform: asset.waveform,
    };
    let cues: TimelineSubtitleCue[] = [];
    if (input.createCaptions) {
      try {
        const result = await transcribeTimelineAudio({
          storageRel: asset.storageRel,
          offset: start,
          workflowId: props.workflowId,
          nodeId: clip.id,
        });
        cues = result.cues.map((cue) => ({ ...cue, id: makeTimelineId('subtitle'), sourceAudioId: clip.id }));
      } catch {
        cues = captionsFromText(input.text, clip.duration, start).map((cue) => ({ ...cue, sourceAudioId: clip.id }));
      }
    }
    timeline.value.audioClips.push(clip);
    timeline.value.subtitles.push(...cues);
    timeline.value.subtitles.sort((left, right) => left.start - right.start);
    markDirty();
    select('audio', clip.id);
    aiPanelOpen.value = false;
    emit('notify', cues.length ? `AI 旁白和 ${cues.length} 条字幕已加入时间线。` : 'AI 旁白已加入时间线。');
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    generatingVoiceover.value = false;
  }
}

async function transcribeSelectedAudio() {
  const clip = selectedAudioClip.value;
  if (!clip?.storageRel || transcribingId.value) return;
  transcribingId.value = clip.id;
  try {
    const result = await transcribeTimelineAudio({
      storageRel: clip.storageRel,
      offset: clip.start,
      prompt: clip.script || '',
      workflowId: props.workflowId,
      nodeId: clip.id,
    });
    const cues = result.cues.map((cue) => ({ ...cue, id: makeTimelineId('subtitle'), sourceAudioId: clip.id }));
    timeline.value.subtitles = timeline.value.subtitles
      .filter((cue) => cue.sourceAudioId !== clip.id)
      .concat(cues)
      .sort((left, right) => left.start - right.start);
    markDirty();
    emit('notify', `已根据语音生成 ${cues.length} 条字幕。`);
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    transcribingId.value = undefined;
  }
}

function addSubtitle() {
  const previous = timeline.value.subtitles.at(-1);
  const start = Math.min(duration.value, previous?.end || 0);
  const cue: TimelineSubtitleCue = {
    id: makeTimelineId('subtitle'),
    start,
    end: Math.min(Math.max(start + 2.5, 2.5), Math.max(duration.value, start + 2.5)),
    text: '输入字幕内容',
  };
  timeline.value.subtitles.push(cue);
  select('subtitle', cue.id);
  markDirty();
}

async function importSubtitles(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    const cues = parseSrt(await file.text());
    timeline.value.subtitles = cues;
    select('subtitle', cues[0]?.id);
    markDirty();
    emit('notify', `已导入 ${cues.length} 条字幕。`);
  } catch (error) {
    emit('notify', messageFrom(error));
  }
}

function removeSelected() {
  const label = selectionKind.value === 'video' ? '视频片段' : selectionKind.value === 'audio' ? '音频素材' : '字幕';
  if (
    selectionKind.value === 'video'
    && selectedVideoClip.value?.track === 0
    && primaryVideoClips.value.length <= 1
    && timeline.value.videoClips.length > 1
  ) {
    emit('notify', '主视频轨至少需要保留一个片段。');
    return;
  }
  if (!selectedId.value || !window.confirm(`确定从时间线删除当前${label}吗？`)) return;
  if (selectionKind.value === 'video') {
    timeline.value.videoClips = timeline.value.videoClips.filter((clip) => clip.id !== selectedId.value);
  } else if (selectionKind.value === 'audio') {
    timeline.value.subtitles = timeline.value.subtitles.filter((cue) => cue.sourceAudioId !== selectedId.value);
    timeline.value.audioClips = timeline.value.audioClips.filter((clip) => clip.id !== selectedId.value);
  } else if (selectionKind.value === 'subtitle') {
    timeline.value.subtitles = timeline.value.subtitles.filter((cue) => cue.id !== selectedId.value);
  }
  selectedId.value = undefined;
  selectionKind.value = 'output';
  playhead.value = Math.min(playhead.value, duration.value);
  markDirty();
}

function updateOutput(updates: Partial<TimelineOutputSettings>) {
  Object.assign(timeline.value.output, updates);
  markDirty();
}

async function exportVideo() {
  if (exporting.value) return;
  if (!primaryVideoClips.value.length) {
    emit('notify', '请先在主视频轨加入至少一个视频片段。');
    return;
  }
  const invalidCue = timeline.value.subtitles.find((cue) => !cue.text.trim() || cue.end <= cue.start);
  if (invalidCue) {
    select('subtitle', invalidCue.id);
    emit('notify', '请修正字幕的内容和时间范围。');
    return;
  }
  persist();
  exporting.value = true;
  compositionController?.abort();
  compositionController = new AbortController();
  try {
    const taskId = makeTimelineId('composition');
    let task = await createCompositionTask({
      clientTaskId: taskId,
      workflowId: props.workflowId,
      timeline: timeline.value,
      source: 'canvas',
      canvasUnits: 1,
    });
    compositionTask.value = task;
    emit('update:latestComposition', task);
    if (!['success', 'error', 'canceled'].includes(task.status)) {
      task = await pollCompositionTask(taskId, (current) => {
        compositionTask.value = current;
        emit('update:latestComposition', current);
      }, compositionController.signal);
    }
    compositionTask.value = task;
    emit('update:latestComposition', task);
    emit('save', cloneTimeline(timeline.value) || timeline.value, task);
    if (task.status !== 'success') throw new Error(task.error || '视频合成失败。');
    previewMode.value = 'result';
    emit('notify', '长视频合成完成。');
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) emit('notify', messageFrom(error));
  } finally {
    exporting.value = false;
  }
}

async function downloadResult() {
  if (!resultUrl.value || downloading.value) return;
  downloading.value = true;
  try {
    const safeTitle = props.workflowTitle.trim().replace(/[\\/:*?"<>|]+/g, '-') || 'canvas-video';
    await downloadComposition(resultUrl.value, `${safeTitle}.mp4`);
    emit('notify', '视频下载已开始。');
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    downloading.value = false;
  }
}

async function downloadTask(task: CompositionTask) {
  const url = task.result_url || task.resultUrl || '';
  if (!url) return;
  try {
    const safeTitle = props.workflowTitle.trim().replace(/[\\/:*?"<>|]+/g, '-') || 'canvas-video';
    await downloadComposition(url, `${safeTitle}-${task.id.slice(-6)}.mp4`);
  } catch (error) {
    emit('notify', messageFrom(error));
  }
}

function previewTask(task: CompositionTask) {
  if (!(task.result_url || task.resultUrl)) return;
  compositionTask.value = task;
  emit('update:latestComposition', task);
  previewMode.value = 'result';
  taskPanelOpen.value = false;
}

async function removeTask(task: CompositionTask) {
  if (['queued', 'running'].includes(task.status) || deletingTaskId.value) return;
  if (!window.confirm('确定永久删除这条合成记录和对应的成片文件吗？删除后无法恢复。')) return;
  deletingTaskId.value = task.id;
  try {
    await deleteCompositionTask(task.id);
    compositionTasks.value = compositionTasks.value.filter((item) => item.id !== task.id);
    if (compositionTask.value?.id === task.id) {
      compositionTask.value = undefined;
      emit('update:latestComposition', undefined);
      previewMode.value = 'clip';
      emit('save', cloneTimeline(timeline.value) || timeline.value, undefined);
    }
    emit('notify', '合成记录和成片文件已永久删除。');
  } catch (error) {
    emit('notify', messageFrom(error));
  } finally {
    deletingTaskId.value = undefined;
  }
}

async function cancelTask(task: CompositionTask) {
  if (task.status !== 'queued') return;
  try {
    const updated = await cancelCompositionTask(task.id);
    compositionTasks.value = compositionTasks.value.map((item) => item.id === updated.id ? updated : item);
    if (compositionTask.value?.id === updated.id) compositionTask.value = updated;
    emit('notify', '合成任务已取消。');
  } catch (error) {
    emit('notify', messageFrom(error));
  }
}

async function retryTask(task: CompositionTask) {
  if (!['error', 'canceled'].includes(task.status)) return;
  try {
    const updated = await retryCompositionTask(task.id);
    compositionTasks.value = compositionTasks.value.map((item) => item.id === updated.id ? updated : item);
    emit('notify', '合成任务已重新排队。');
  } catch (error) {
    emit('notify', messageFrom(error));
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.open) return;
  const target = event.target as HTMLElement;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    persist();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    undoEdit();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.shiftKey && event.key.toLowerCase() === 'z'))) {
    event.preventDefault();
    redoEdit();
    return;
  }
  if (target.matches('input, textarea, select')) return;
  if (event.code === 'Space') {
    event.preventDefault();
    toggleTimelinePlayback();
    return;
  }
  if (event.key.toLowerCase() === 's') {
    event.preventDefault();
    splitSelectedClip();
    return;
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId.value) {
    event.preventDefault();
    removeSelected();
    return;
  }
  if (event.key === 'Escape') closeWorkbench();
}

watch(() => props.open, (open) => {
  if (open) window.addEventListener('keydown', handleKeydown);
  else {
    window.removeEventListener('keydown', handleKeydown);
    timelinePlaying.value = false;
    window.cancelAnimationFrame(playbackFrame);
    pauseTimelineAudio();
    pauseOverlayVideos();
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('pointermove', moveTimedDrag);
  window.cancelAnimationFrame(playbackFrame);
  pauseTimelineAudio();
  pauseOverlayVideos();
  compositionController?.abort();
});
</script>

<template>
  <Transition name="timeline-workbench">
    <section v-if="open" class="video-timeline-workbench" aria-label="时间线工作台">
      <header class="timeline-topbar">
        <div class="timeline-topbar-title">
          <button class="timeline-icon-button" type="button" title="返回画布" aria-label="返回画布" @click="closeWorkbench">
            <ArrowLeft :size="18" aria-hidden="true" />
          </button>
          <span class="timeline-product-icon"><Film :size="18" aria-hidden="true" /></span>
          <div>
            <strong>时间线工作台</strong>
            <small>{{ workflowTitle }} · {{ timeline.videoClips.length }} 个片段</small>
          </div>
        </div>

        <div class="timeline-project-summary">
          <span>{{ formatTimelineTime(duration) }}</span>
          <i aria-hidden="true" />
          <span>{{ timeline.output.aspectRatio }}</span>
          <i aria-hidden="true" />
          <span>{{ timeline.output.resolution.toUpperCase() }}</span>
        </div>

        <div class="timeline-topbar-actions">
          <span class="timeline-toolbar-group" aria-label="编辑历史">
            <button class="timeline-icon-button" type="button" title="撤销" aria-label="撤销" :disabled="!canUndo" @click="undoEdit"><Undo2 :size="16" /></button>
            <button class="timeline-icon-button" type="button" title="重做" aria-label="重做" :disabled="!canRedo" @click="redoEdit"><Redo2 :size="16" /></button>
          </span>
          <button class="timeline-icon-button" type="button" title="AI 配音" aria-label="打开 AI 配音" @click="aiPanelOpen = !aiPanelOpen; loadCapabilities()">
            <Sparkles :size="17" />
          </button>
          <button class="timeline-icon-button" type="button" title="合成记录" aria-label="打开合成记录" @click="openTaskPanel">
            <History :size="17" />
          </button>
          <button class="timeline-secondary-button" type="button" :disabled="saving" @click="persist">
            <LoaderCircle v-if="saving" class="timeline-spin" :size="16" aria-hidden="true" />
            <Save v-else :size="16" aria-hidden="true" />
            {{ saving ? '保存中' : '保存' }}
          </button>
          <button class="timeline-primary-button" type="button" :disabled="exporting || !primaryVideoClips.length" @click="exportVideo">
            <LoaderCircle v-if="exporting" class="timeline-spin" :size="16" aria-hidden="true" />
            <WandSparkles v-else :size="16" aria-hidden="true" />
            {{ exporting ? compositionTask?.progress || '正在合成' : '合成长视频' }}
          </button>
        </div>
      </header>

      <div class="timeline-editor-main">
        <section class="timeline-preview-pane" aria-label="视频预览">
          <div class="timeline-preview-tabs" role="tablist" aria-label="预览类型">
            <button :class="{ active: previewMode === 'clip' }" type="button" role="tab" :aria-selected="previewMode === 'clip'" @click="previewMode = 'clip'">片段预览</button>
            <button :class="{ active: previewMode === 'result' }" type="button" role="tab" :aria-selected="previewMode === 'result'" :disabled="!resultUrl" @click="previewMode = 'result'">成片预览</button>
          </div>

          <div class="timeline-preview-stage" :data-aspect="timeline.output.aspectRatio" :style="{ backgroundColor: timeline.output.backgroundColor }">
            <video v-if="previewMode === 'result' && resultUrl" class="timeline-preview-result" :src="resultUrl" controls preload="metadata" />
            <video
              v-else-if="previewClip"
              ref="previewVideo"
              :key="previewClip.id"
              class="timeline-preview-primary"
              :src="previewClip.sourceUrl"
              :muted="previewClip.muted"
              :volume="Math.min(1, previewClip.volume)"
              :style="previewVisualStyle"
              playsinline
              preload="metadata"
              @loadedmetadata="handlePreviewMetadata"
              @timeupdate="handlePreviewTimeUpdate"
              @play="handlePreviewPlay"
              @pause="handlePreviewPause"
            />
            <video
              v-for="placement in overlayVideoPlacements"
              v-show="previewMode === 'clip' && previewClip"
              :key="`overlay-${placement.clip.id}`"
              :ref="(element) => setOverlayVideoElement(element, placement.clip.id)"
              class="timeline-preview-overlay"
              :class="{ active: activeOverlayPlacements.some((item) => item.clip.id === placement.clip.id) }"
              :src="placement.clip.sourceUrl"
              :muted="placement.clip.muted"
              :volume="Math.min(1, placement.clip.volume)"
              :style="overlayPreviewStyle(placement)"
              playsinline
              preload="metadata"
              aria-hidden="true"
              @loadedmetadata="handleOverlayMetadata($event, placement.clip.id)"
            />
            <div v-if="previewMode !== 'result' && !previewClip" class="timeline-preview-empty">
              <Video :size="28" aria-hidden="true" />
              <strong>还没有可预览的视频</strong>
              <span>把视频文件拖入下方主视频轨，或点击轨道左侧的上传按钮。</span>
            </div>
          </div>

          <footer class="timeline-preview-footer">
            <div class="timeline-preview-navigation">
              <button class="timeline-icon-button" type="button" :title="timelinePlaying ? '暂停时间线' : '播放时间线'" :aria-label="timelinePlaying ? '暂停时间线' : '播放时间线'" :disabled="!primaryVideoClips.length" @click="toggleTimelinePlayback">
                <Pause v-if="timelinePlaying" :size="17" />
                <Play v-else :size="17" />
              </button>
              <b>{{ formatTimelineTime(playhead) }}</b>
              <button class="timeline-icon-button" type="button" :title="selectedVideoClip?.track ? '向前移动 0.1 秒' : '片段前移'" :aria-label="selectedVideoClip?.track ? '向前移动 0.1 秒' : '片段前移'" :disabled="!selectedVideoClip" @click="moveSelected(-1)"><ChevronLeft :size="18" /></button>
              <span v-if="selectedVideoClip">{{ selectedVideoClip.title }}</span>
              <span v-else>选择时间线中的片段</span>
              <button class="timeline-icon-button" type="button" :title="selectedVideoClip?.track ? '向后移动 0.1 秒' : '片段后移'" :aria-label="selectedVideoClip?.track ? '向后移动 0.1 秒' : '片段后移'" :disabled="!selectedVideoClip" @click="moveSelected(1)"><ChevronRight :size="18" /></button>
            </div>
            <div v-if="compositionTask" class="timeline-render-status" :data-status="compositionTask.status" aria-live="polite">
              <i aria-hidden="true" />
              <span>{{ compositionTask.progress || compositionTask.status }}</span>
              <small v-if="compositionTask.size">{{ formatTimelineBytes(compositionTask.size) }}</small>
              <button v-if="resultUrl" class="timeline-icon-button" type="button" title="下载成片" aria-label="下载成片" :disabled="downloading" @click="downloadResult">
                <LoaderCircle v-if="downloading" class="timeline-spin" :size="16" />
                <Download v-else :size="16" />
              </button>
            </div>
          </footer>
        </section>

        <TimelineVoiceoverPanel
          v-if="aiPanelOpen"
          :capabilities="capabilities"
          :loading="loadingCapabilities"
          :generating="generatingVoiceover"
          @close="aiPanelOpen = false"
          @generate="generateVoiceover"
        />
        <TimelineInspector
          v-else
          :video-clip="selectedVideoClip"
          :audio-clip="selectedAudioClip"
          :subtitle="selectedSubtitle"
          :output="timeline.output"
          :duration="duration"
          :transcribing="transcribingId === selectedAudioClip?.id"
          @update-video="updateSelectedVideo"
          @update-audio="selectedAudioClip && updateAudioClip(selectedAudioClip.id, $event)"
          @update-subtitle="selectedSubtitle && updateSubtitle(selectedSubtitle.id, $event)"
          @update-output="updateOutput"
          @remove="removeSelected"
          @transcribe="transcribeSelectedAudio"
        />
      </div>

      <section class="timeline-panel" aria-label="媒体时间线">
        <header class="timeline-panel-toolbar">
          <div class="timeline-panel-summary">
            <button class="timeline-icon-button" type="button" :title="timelinePlaying ? '暂停' : '播放'" :aria-label="timelinePlaying ? '暂停' : '播放'" :disabled="!primaryVideoClips.length" @click="toggleTimelinePlayback">
              <Pause v-if="timelinePlaying" :size="16" />
              <Play v-else :size="16" />
            </button>
            <strong>时间线</strong>
            <span>{{ timeline.videoClips.length }} 段视频 · {{ timeline.audioClips.length }} 段音频 · {{ timeline.subtitles.length }} 条字幕</span>
            <b>{{ formatTimelineTime(playhead) }} / {{ formatTimelineTime(duration) }}</b>
          </div>
          <div class="timeline-zoom-control">
            <ZoomOut :size="15" aria-hidden="true" />
            <input v-model.number="pixelsPerSecond" type="range" min="22" max="64" step="2" aria-label="时间线缩放" />
            <ZoomIn :size="15" aria-hidden="true" />
          </div>
          <span class="timeline-toolbar-group">
            <button class="timeline-icon-button" type="button" title="在播放头处切割片段" aria-label="在播放头处切割片段" :disabled="!selectedVideoClip" @click="splitSelectedClip"><Scissors :size="16" /></button>
            <button class="timeline-icon-button" type="button" title="输出设置" aria-label="输出设置" @click="select('output')"><Settings2 :size="17" aria-hidden="true" /></button>
          </span>
        </header>

        <div class="timeline-scroll-area">
          <div class="timeline-ruler-row" :style="{ minWidth: `${timelineWidth + 168}px` }">
            <span class="timeline-ruler-spacer">时间</span>
            <div class="timeline-ruler-interactive" @pointerdown="seekFromLane">
              <TimelineRuler :duration="duration" :pixels-per-second="pixelsPerSecond" />
              <span class="timeline-playhead timeline-playhead-ruler" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
            </div>
          </div>

          <TimelineTrack class="timeline-video-track" label="主视频" :description="`${primaryVideoClips.length} 个顺序片段`" :icon="Film">
            <template #action>
              <button class="timeline-track-action" type="button" title="上传到主视频轨" aria-label="上传到主视频轨" :disabled="Boolean(uploadingKind)" @click="chooseVideoUpload(0)">
                <LoaderCircle v-if="uploadingKind === 'video' && pendingVideoTrack === 0" class="timeline-spin" :size="14" />
                <Upload v-else :size="14" />
              </button>
              <input ref="videoInput" class="timeline-hidden-input" type="file" accept="video/*,.mp4,.webm,.mov,.m4v,.avi,.mkv" @change="handleVideoUpload" />
            </template>
            <div
              class="timeline-lane-content timeline-video-lane"
              :class="{ 'is-drop-target': dragTargetTrack === 0 }"
              :style="{ width: `${timelineWidth}px` }"
              data-video-track="0"
              @pointerdown.self="seekFromLane"
              @dragover="indicateDropTrack($event, 0)"
              @dragleave.self="dragTargetTrack = undefined"
              @drop="dropClipOnTrack($event, 0)"
            >
              <TimelineClip
                v-for="placement in videoPlacementsByTrack[0]"
                :key="placement.clip.id"
                :clip="placement.clip"
                :selected="selectionKind === 'video' && selectedId === placement.clip.id"
                :width="placement.width"
                :index="placement.index"
                :style="{ left: `${placement.left}px` }"
                @select="select('video', placement.clip.id)"
                @dragstart="beginClipDrag($event, placement.clip.id)"
                @dragend="endClipDrag"
              />
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="uploadingKind === 'video' && pendingVideoTrack === 0" class="timeline-lane-empty is-uploading"><LoaderCircle class="timeline-spin" :size="14" /> 正在上传视频</span>
              <span v-else-if="!primaryVideoClips.length" class="timeline-lane-empty">拖入视频文件，或点击左侧上传按钮</span>
            </div>
          </TimelineTrack>

          <TimelineTrack class="timeline-video-track" label="叠加轨 1" :description="`${videoPlacementsByTrack[1].length} 个覆盖片段`" :icon="Layers">
            <template #action>
              <button class="timeline-track-action" type="button" title="上传到叠加轨 1" aria-label="上传到叠加轨 1" :disabled="Boolean(uploadingKind) || !primaryVideoClips.length" @click="chooseVideoUpload(1)">
                <LoaderCircle v-if="uploadingKind === 'video' && pendingVideoTrack === 1" class="timeline-spin" :size="14" />
                <Upload v-else :size="14" />
              </button>
            </template>
            <div
              class="timeline-lane-content timeline-video-lane timeline-overlay-lane"
              :class="{ 'is-drop-target': dragTargetTrack === 1 }"
              :style="{ width: `${timelineWidth}px` }"
              data-video-track="1"
              @pointerdown.self="seekFromLane"
              @dragover="indicateDropTrack($event, 1)"
              @dragleave.self="dragTargetTrack = undefined"
              @drop="dropClipOnTrack($event, 1)"
            >
              <TimelineClip
                v-for="placement in videoPlacementsByTrack[1]"
                :key="placement.clip.id"
                :clip="placement.clip"
                :selected="selectionKind === 'video' && selectedId === placement.clip.id"
                :width="placement.width"
                :index="placement.index"
                :style="{ left: `${placement.left}px` }"
                @select="select('video', placement.clip.id)"
                @dragstart="beginClipDrag($event, placement.clip.id)"
                @dragend="endClipDrag"
              />
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="uploadingKind === 'video' && pendingVideoTrack === 1" class="timeline-lane-empty is-uploading"><LoaderCircle class="timeline-spin" :size="14" /> 正在上传视频</span>
              <span v-else-if="!videoPlacementsByTrack[1].length" class="timeline-lane-empty">拖入视频作为画中画或覆盖素材</span>
            </div>
          </TimelineTrack>

          <TimelineTrack class="timeline-video-track" label="叠加轨 2" :description="`${videoPlacementsByTrack[2].length} 个顶层片段`" :icon="Layers">
            <template #action>
              <button class="timeline-track-action" type="button" title="上传到叠加轨 2" aria-label="上传到叠加轨 2" :disabled="Boolean(uploadingKind) || !primaryVideoClips.length" @click="chooseVideoUpload(2)">
                <LoaderCircle v-if="uploadingKind === 'video' && pendingVideoTrack === 2" class="timeline-spin" :size="14" />
                <Upload v-else :size="14" />
              </button>
            </template>
            <div
              class="timeline-lane-content timeline-video-lane timeline-overlay-lane"
              :class="{ 'is-drop-target': dragTargetTrack === 2 }"
              :style="{ width: `${timelineWidth}px` }"
              data-video-track="2"
              @pointerdown.self="seekFromLane"
              @dragover="indicateDropTrack($event, 2)"
              @dragleave.self="dragTargetTrack = undefined"
              @drop="dropClipOnTrack($event, 2)"
            >
              <TimelineClip
                v-for="placement in videoPlacementsByTrack[2]"
                :key="placement.clip.id"
                :clip="placement.clip"
                :selected="selectionKind === 'video' && selectedId === placement.clip.id"
                :width="placement.width"
                :index="placement.index"
                :style="{ left: `${placement.left}px` }"
                @select="select('video', placement.clip.id)"
                @dragstart="beginClipDrag($event, placement.clip.id)"
                @dragend="endClipDrag"
              />
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="uploadingKind === 'video' && pendingVideoTrack === 2" class="timeline-lane-empty is-uploading"><LoaderCircle class="timeline-spin" :size="14" /> 正在上传视频</span>
              <span v-else-if="!videoPlacementsByTrack[2].length" class="timeline-lane-empty">拖入视频作为最高层覆盖素材</span>
            </div>
          </TimelineTrack>

          <TimelineTrack label="旁白" :description="`${timeline.audioClips.filter((clip) => clip.kind === 'voiceover').length} 段`" :icon="Mic2">
            <template #action>
              <button class="timeline-track-action" type="button" title="上传旁白" aria-label="上传旁白" :disabled="Boolean(uploadingKind)" @click="voiceoverInput?.click()">
                <LoaderCircle v-if="uploadingKind === 'voiceover'" class="timeline-spin" :size="14" />
                <Upload v-else :size="14" />
              </button>
              <input ref="voiceoverInput" class="timeline-hidden-input" type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac" @change="handleAudioUpload($event, 'voiceover')" />
            </template>
            <div class="timeline-lane-content" :style="{ width: `${timelineWidth}px` }" @pointerdown.self="seekFromLane">
              <button
                v-for="clip in timeline.audioClips.filter((item) => item.kind === 'voiceover')"
                :key="clip.id"
                class="timeline-audio-clip voiceover"
                :class="{ selected: selectionKind === 'audio' && selectedId === clip.id }"
                :style="{ left: `${clip.start * pixelsPerSecond}px`, width: `${Math.max(92, clip.duration * pixelsPerSecond)}px` }"
                type="button"
                @click="select('audio', clip.id)"
                @pointerdown="beginTimedDrag($event, 'audio', clip.id)"
              >
                <span class="timeline-waveform" aria-hidden="true"><i v-for="(peak, index) in (clip.waveform?.length ? clip.waveform : Array(36).fill(0.4))" :key="index" :style="{ height: `${Math.max(8, peak * 100)}%` }" /></span>
                <strong>{{ clip.title }}</strong>
              </button>
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="!timeline.audioClips.some((clip) => clip.kind === 'voiceover')" class="timeline-lane-empty">上传录音或配音文件</span>
            </div>
          </TimelineTrack>

          <TimelineTrack label="音乐" :description="`${timeline.audioClips.filter((clip) => clip.kind === 'music').length} 段`" :icon="Music2">
            <template #action>
              <button class="timeline-track-action" type="button" title="添加音乐" aria-label="添加音乐" :disabled="Boolean(uploadingKind)" @click="musicInput?.click()">
                <LoaderCircle v-if="uploadingKind === 'music'" class="timeline-spin" :size="14" />
                <Plus v-else :size="14" />
              </button>
              <input ref="musicInput" class="timeline-hidden-input" type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac" @change="handleAudioUpload($event, 'music')" />
            </template>
            <div class="timeline-lane-content" :style="{ width: `${timelineWidth}px` }" @pointerdown.self="seekFromLane">
              <button
                v-for="clip in timeline.audioClips.filter((item) => item.kind === 'music')"
                :key="clip.id"
                class="timeline-audio-clip music"
                :class="{ selected: selectionKind === 'audio' && selectedId === clip.id }"
                :style="{ left: `${clip.start * pixelsPerSecond}px`, width: `${Math.max(92, clip.duration * pixelsPerSecond)}px` }"
                type="button"
                @click="select('audio', clip.id)"
                @pointerdown="beginTimedDrag($event, 'audio', clip.id)"
              >
                <span class="timeline-waveform" aria-hidden="true"><i v-for="(peak, index) in (clip.waveform?.length ? clip.waveform : Array(48).fill(0.35))" :key="index" :style="{ height: `${Math.max(8, peak * 100)}%` }" /></span>
                <strong>{{ clip.title }}</strong>
              </button>
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="!timeline.audioClips.some((clip) => clip.kind === 'music')" class="timeline-lane-empty">添加背景音乐</span>
            </div>
          </TimelineTrack>

          <TimelineTrack label="字幕" :description="`${timeline.subtitles.length} 条`" :icon="Captions">
            <template #action>
              <button class="timeline-track-action" type="button" title="导入 SRT 字幕" aria-label="导入 SRT 字幕" @click="subtitleInput?.click()"><Upload :size="14" /></button>
              <button class="timeline-track-action" type="button" title="添加字幕" aria-label="添加字幕" @click="addSubtitle"><Plus :size="14" /></button>
              <input ref="subtitleInput" class="timeline-hidden-input" type="file" accept=".srt,application/x-subrip,text/plain" @change="importSubtitles" />
            </template>
            <div class="timeline-lane-content" :style="{ width: `${timelineWidth}px` }" @pointerdown.self="seekFromLane">
              <button
                v-for="cue in timeline.subtitles"
                :key="cue.id"
                class="timeline-subtitle-clip"
                :class="{ selected: selectionKind === 'subtitle' && selectedId === cue.id }"
                :style="{ left: `${cue.start * pixelsPerSecond}px`, width: `${Math.max(72, (cue.end - cue.start) * pixelsPerSecond)}px` }"
                type="button"
                :title="cue.text"
                @click="select('subtitle', cue.id)"
                @pointerdown="beginTimedDrag($event, 'subtitle', cue.id)"
              >
                {{ cue.text }}
              </button>
              <span class="timeline-playhead" :style="{ left: `${playhead * pixelsPerSecond}px` }" />
              <span v-if="!timeline.subtitles.length" class="timeline-lane-empty">添加字幕或导入 SRT 文件</span>
            </div>
          </TimelineTrack>
        </div>
      </section>
      <TimelineTaskPanel
        v-if="taskPanelOpen"
        :tasks="compositionTasks"
        :loading="loadingTasks"
        :deleting-id="deletingTaskId"
        @close="taskPanelOpen = false"
        @refresh="loadTasks"
        @preview="previewTask"
        @download="downloadTask"
        @remove="removeTask"
        @cancel="cancelTask"
        @retry="retryTask"
      />
      <div class="timeline-audio-preview-elements" aria-hidden="true">
        <audio
          v-for="clip in timeline.audioClips"
          :key="clip.id"
          :ref="(element) => setAudioElement(element, clip.id)"
          :src="clip.sourceUrl"
          preload="metadata"
        />
      </div>
    </section>
  </Transition>
</template>
