<script setup lang="ts">
import { computed, ref } from 'vue';
import {
  Check,
  Download,
  Image as ImageIcon,
  Link2,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Pin,
  RefreshCw,
  Trash2,
  Upload,
  Video,
  X,
} from '@lucide/vue';

import type { VideoGenerationModelSpec } from '@/lib/api';
import CanvasPromptEditor from '@/features/infinite-canvas/components/CanvasPromptEditor.vue';
import CanvasTextNodeBody from '@/features/infinite-canvas/components/CanvasTextNodeBody.vue';
import {
  type CanvasMediaReference,
  type CanvasNode as CanvasNodeModel,
} from '@/features/infinite-canvas/types';

const props = defineProps<{
  node: CanvasNodeModel;
  nodeWidth: number;
  stageHeight: number;
  nodeHeight: number;
  promptWidth: number;
  zoom: number;
  connectionOffset: number;
  selected: boolean;
  primarySelected: boolean;
  connectionSource: boolean;
  connectionImageIndex?: number;
  dragging: boolean;
  connectionTarget: boolean;
  connectionTargetOffset?: number;
  imageEnabled: boolean;
  videoEnabled: boolean;
  uploadEnabled: boolean;
  imageModels: string[];
  videoModels: VideoGenerationModelSpec[];
  videoMode: 'text_to_video' | 'image_to_video';
  promptReferences: CanvasMediaReference[];
  referenceCandidates: CanvasMediaReference[];
  downloading: boolean;
  downloadingImageIndex?: number;
  retryingVideoAnalysis: boolean;
}>();

const emit = defineEmits<{
  pointerdown: [event: PointerEvent];
  select: [];
  updatePrompt: [value: string];
  updateContent: [value: string];
  updateNode: [payload: { key: keyof CanvasNodeModel; value: CanvasNodeModel[keyof CanvasNodeModel] }];
  setPrimaryImage: [imageIndex: number];
  toggleGallery: [];
  generate: [];
  uploadMedia: [file: File];
  removeReference: [referenceId: string];
  addPromptReference: [sourceNodeId: string];
  removePromptReference: [sourceNodeId: string];
  contextMenu: [event: MouseEvent];
  startConnection: [event: PointerEvent, imageIndex?: number];
  finishConnection: [event: PointerEvent];
  connectionTargetEnter: [];
  connectionTargetLeave: [];
  remove: [];
  download: [];
  downloadImage: [imageIndex: number];
  retryVideoAnalysis: [];
}>();

const fileInput = ref<HTMLInputElement>();
const mediaDragActive = ref(false);

const isMediaNode = computed(() => props.node.type === 'image' || props.node.type === 'video');
const imageResultUrls = computed(() => {
  const values = Array.isArray(props.node.resultUrls) && props.node.resultUrls.length
    ? props.node.resultUrls
    : props.node.resultUrl
      ? [props.node.resultUrl]
      : [];
  return values.filter(Boolean);
});
const hasMultipleImageResults = computed(() => props.node.type === 'image' && imageResultUrls.value.length > 1);
const primaryImageIndex = computed(() => {
  const requested = Number(props.node.primaryImageIndex);
  return Number.isInteger(requested) && requested >= 0 && requested < imageResultUrls.value.length
    ? requested
    : 0;
});
const primaryImageUrl = computed(() => imageResultUrls.value[primaryImageIndex.value] || imageResultUrls.value[0] || '');
const stackedImageLayers = computed(() => {
  const urls = imageResultUrls.value
    .filter((_, index) => index !== primaryImageIndex.value)
    .slice(0, 2);
  return urls.map((url, index) => ({
    url,
    depth: urls.length - index,
  }));
});
const galleryExpanded = computed(() => hasMultipleImageResults.value && Boolean(props.node.galleryExpanded));
const galleryStyle = computed(() => ({
  gridTemplateColumns: `repeat(${imageResultUrls.value.length}, minmax(0, 1fr))`,
}));
const mediaPreviewUrl = computed(() => {
  if (primaryImageUrl.value) return primaryImageUrl.value;
  if (props.node.resultUrl) return props.node.resultUrl;
  if (props.node.type === 'video') return props.node.inputUrl;
  return [...props.node.references]
    .reverse()
    .find((reference) => reference.uploadState !== 'failed')?.previewUrl;
});
const videoAnalysisStatus = computed(() => String(props.node.videoAnalysisStatus || 'pending').toLowerCase());
const showsVideoAnalysisStatus = computed(() => props.node.type === 'video'
  && Boolean(props.node.inputVideoId)
  && !props.node.resultUrl
  && props.node.status !== 'uploading'
  && props.node.status !== 'generating'
  && props.node.status !== 'failed');
const videoAnalysisFailed = computed(() => showsVideoAnalysisStatus.value && videoAnalysisStatus.value === 'failed');
const displayStatusClass = computed(() => {
  if (!showsVideoAnalysisStatus.value) return props.node.status;
  if (videoAnalysisStatus.value === 'failed') return 'failed';
  if (videoAnalysisStatus.value === 'ready') return 'ready';
  return 'generating';
});
const displayStatusBusy = computed(() => props.node.status === 'generating'
  || props.node.status === 'uploading'
  || (showsVideoAnalysisStatus.value && ['pending', 'queued', 'processing', 'analyzing'].includes(videoAnalysisStatus.value)));
const statusLabel = computed(() => {
  if ((props.node.status === 'generating' || props.node.status === 'uploading') && props.node.progress) {
    return props.node.progress;
  }
  if (showsVideoAnalysisStatus.value) {
    if (videoAnalysisStatus.value === 'ready') return '已解析';
    if (videoAnalysisStatus.value === 'failed') return '解析失败';
    if (videoAnalysisStatus.value === 'queued') return '等待解析';
    return '解析中';
  }
  const labels: Record<CanvasNodeModel['status'], string> = {
    idle: '待生成',
    uploading: '上传中',
    generating: '生成中',
    ready: '已完成',
    failed: '失败',
  };
  return labels[props.node.status];
});
const costLabel = computed(() => {
  if (typeof props.node.cost !== 'number' || Number.isNaN(props.node.cost)) return '';
  return `￥${props.node.cost.toFixed(props.node.cost >= 1 ? 3 : 4)}`;
});
const nodeStyle = computed(() => ({
  '--canvas-node-transform': `translate(${props.node.x}px, ${props.node.y}px)`,
  '--canvas-prompt-editor-scale': String(1 / Math.max(props.zoom, 0.01)),
  '--canvas-prompt-editor-width': `${props.promptWidth}px`,
  ...(isMediaNode.value ? { width: `${props.nodeWidth}px` } : {}),
  ...(isMediaNode.value ? { height: `${props.nodeHeight}px` } : {}),
}));
const mediaStageStyle = computed(() => ({
  height: `${props.stageHeight}px`,
  minHeight: `${props.stageHeight}px`,
  flexBasis: `${props.stageHeight}px`,
}));
const inputPortStyle = computed(() => {
  const offset = props.connectionTargetOffset ?? (isMediaNode.value ? props.connectionOffset : undefined);
  return offset === undefined ? undefined : { top: `${offset - 6.5}px` };
});
const outputPortStyle = computed(() => isMediaNode.value
  ? { top: `${props.connectionOffset - 6.5}px` }
  : undefined);

function updateField(key: keyof CanvasNodeModel, value: CanvasNodeModel[keyof CanvasNodeModel]) {
  emit('updateNode', { key, value });
}

function forwardSelect() {
  emit('select');
}

function forwardPrompt(value: string) {
  emit('updatePrompt', value);
}

function forwardNodeUpdate(payload: { key: keyof CanvasNodeModel; value: CanvasNodeModel[keyof CanvasNodeModel] }) {
  emit('updateNode', payload);
}

function forwardAddPromptReference(sourceNodeId: string) {
  emit('addPromptReference', sourceNodeId);
}

function forwardRemovePromptReference(sourceNodeId: string) {
  emit('removePromptReference', sourceNodeId);
}

function updateMediaAspectRatio(event: Event) {
  const media = event.currentTarget as HTMLImageElement | HTMLVideoElement;
  const width = media instanceof HTMLVideoElement ? media.videoWidth : media.naturalWidth;
  const height = media instanceof HTMLVideoElement ? media.videoHeight : media.naturalHeight;
  if (width <= 0 || height <= 0) return;
  const ratio = width / height;
  if (Math.abs((props.node.previewAspectRatio || 0) - ratio) < 0.0001) return;
  updateField('previewAspectRatio', ratio);
}

function updateMediaMetadata(event: Event) {
  updateMediaAspectRatio(event);
  const media = event.currentTarget;
  if (!(media instanceof HTMLVideoElement) || !Number.isFinite(media.duration) || media.duration <= 0) return;
  const duration = Math.round(media.duration * 1000) / 1000;
  if (Math.abs((props.node.sourceDuration || 0) - duration) < 0.001) return;
  updateField('sourceDuration', duration);
}

function stopAndRemove(event: MouseEvent) {
  event.stopPropagation();
  emit('remove');
}

function handlePointerdown(event: PointerEvent) {
  const target = event.target as Element | null;
  if (target instanceof HTMLVideoElement) {
    const bounds = target.getBoundingClientRect();
    const controlsHeight = Math.min(64, Math.max(40, bounds.height * 0.2));
    if (event.clientY >= bounds.bottom - controlsHeight) return;
  }
  event.stopPropagation();
  emit('pointerdown', event);
}

function handleContextMenu(event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  emit('contextMenu', event);
}

function chooseUpload() {
  emit('select');
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) emit('uploadMedia', file);
  input.value = '';
}

function hasDraggedFiles(event: DragEvent) {
  return Array.from(event.dataTransfer?.types || []).includes('Files');
}

function handleMediaDrag(event: DragEvent) {
  if (!hasDraggedFiles(event) || !props.uploadEnabled) return;
  event.preventDefault();
  event.stopPropagation();
  mediaDragActive.value = true;
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
}

function handleMediaDragLeave(event: DragEvent) {
  const stage = event.currentTarget as HTMLElement;
  if (event.relatedTarget instanceof Node && stage.contains(event.relatedTarget)) return;
  mediaDragActive.value = false;
}

function handleMediaDrop(event: DragEvent) {
  event.preventDefault();
  event.stopPropagation();
  mediaDragActive.value = false;
  const file = event.dataTransfer?.files?.[0];
  if (!file || !props.uploadEnabled) return;
  emit('select');
  emit('uploadMedia', file);
}

function generate() {
  emit('select');
  emit('generate');
}

function toggleGallery() {
  emit('toggleGallery');
}

function setPrimaryImage(imageIndex: number) {
  emit('setPrimaryImage', imageIndex);
}

function startImageConnection(event: PointerEvent, imageIndex: number) {
  emit('setPrimaryImage', imageIndex);
  emit('startConnection', event, imageIndex);
}
</script>

<template>
  <article
    class="canvas-node"
    :data-canvas-node-id="node.id"
    :class="[
      `node-type-${node.type}`,
      {
        'is-selected': selected,
        'is-active': primarySelected,
        'is-connecting': connectionSource,
        'is-dragging': dragging,
        'is-connection-target': connectionTarget,
        'is-failed': node.status === 'failed',
        'has-expanded-gallery': galleryExpanded,
      },
    ]"
    :style="nodeStyle"
    tabindex="0"
    draggable="false"
    @pointerdown="handlePointerdown"
    @contextmenu="handleContextMenu"
  >
    <button
      class="node-port node-port-input"
      type="button"
      title="连接到此节点"
      aria-label="连接到此节点"
      @pointerdown.stop
      @pointerenter.stop="emit('connectionTargetEnter')"
      @pointerleave.stop="emit('connectionTargetLeave')"
      @pointerup.stop="emit('finishConnection', $event)"
      :style="inputPortStyle"
    ><span /></button>

    <div class="canvas-node-visual">
    <template v-if="isMediaNode">
      <header class="media-node-header">
        <div class="media-node-title">
          <span class="media-node-icon">
            <ImageIcon v-if="node.type === 'image'" :size="16" aria-hidden="true" />
            <Video v-else :size="16" aria-hidden="true" />
          </span>
          <strong :title="node.title">{{ node.title }}</strong>
          <Link2 v-if="connectionSource" class="media-connection-icon" :size="14" aria-label="正在连接" />
        </div>
        <div class="media-node-actions">
          <span v-if="costLabel" class="media-cost" :title="`本次生成费用 ${costLabel}`">{{ costLabel }}</span>
          <span class="media-status" :class="`status-${displayStatusClass}`">
            <LoaderCircle v-if="displayStatusBusy" class="spin" :size="12" aria-hidden="true" />
            {{ statusLabel }}
          </span>
          <button
            v-if="videoAnalysisFailed"
            class="node-icon-button"
            type="button"
            title="重试视频解析"
            aria-label="重试视频解析"
            :disabled="retryingVideoAnalysis"
            @pointerdown.stop
            @click.stop="emit('retryVideoAnalysis')"
          >
            <LoaderCircle v-if="retryingVideoAnalysis" class="spin" :size="14" aria-hidden="true" />
            <RefreshCw v-else :size="14" aria-hidden="true" />
          </button>
          <button
            v-if="node.type === 'image' && imageResultUrls.length && node.taskId"
            class="node-icon-button"
            type="button"
            title="下载生成图片"
            aria-label="下载生成图片"
            :disabled="downloading"
            @pointerdown.stop
            @click.stop="emit('download')"
          >
            <LoaderCircle v-if="downloading" class="spin" :size="14" aria-hidden="true" />
            <Download v-else :size="14" aria-hidden="true" />
          </button>
          <button class="node-icon-button" type="button" title="删除节点" aria-label="删除节点" @pointerdown.stop @click="stopAndRemove">
            <Trash2 :size="14" aria-hidden="true" />
          </button>
        </div>
      </header>

      <div
        class="media-node-stage"
        :class="{
          'has-result': Boolean(mediaPreviewUrl),
          'has-image-stack': hasMultipleImageResults && !galleryExpanded,
          'is-gallery-expanded': galleryExpanded,
          'is-file-dragover': mediaDragActive,
        }"
        :style="mediaStageStyle"
        @dragenter="handleMediaDrag"
        @dragover="handleMediaDrag"
        @dragleave="handleMediaDragLeave"
        @drop="handleMediaDrop"
      >
        <div v-if="hasMultipleImageResults && !galleryExpanded" class="media-result-stack" aria-label="已折叠的生成图片结果">
          <div
            v-for="layer in stackedImageLayers"
            :key="`${node.id}-stack-${layer.url}`"
            class="media-result-stack-layer"
            :class="`depth-${layer.depth}`"
            aria-hidden="true"
          >
            <img :src="layer.url" alt="" draggable="false" />
          </div>
          <figure class="media-result-stack-main">
            <img
              :src="primaryImageUrl"
              :alt="`${node.prompt || '生成图片'} 主图 ${primaryImageIndex + 1}`"
              draggable="false"
              @load="updateMediaAspectRatio"
            />
            <span class="media-primary-badge"><Check :size="12" aria-hidden="true" /> 主图 {{ primaryImageIndex + 1 }}</span>
            <button
              class="media-stack-count"
              type="button"
              :title="`展开查看 ${imageResultUrls.length} 张图片`"
              :aria-label="`展开${imageResultUrls.length}张图片`"
              @pointerdown.stop
              @click.stop="toggleGallery"
            ><Maximize2 :size="14" aria-hidden="true" /> {{ imageResultUrls.length }}张</button>
          </figure>
        </div>
        <div
          v-else-if="galleryExpanded"
          class="media-result-gallery"
          :style="galleryStyle"
          :aria-label="`${imageResultUrls.length} 张生成图片`"
        >
          <figure
            v-for="(url, index) in imageResultUrls"
            :key="`${node.id}-result-${index}`"
            class="media-result-card"
            :class="{
              'is-primary': index === primaryImageIndex,
              'is-connection-source': connectionSource && index === connectionImageIndex,
            }"
          >
            <div class="media-result-frame">
              <img
                :src="url"
                :alt="`${node.prompt || '生成图片'} ${index + 1}`"
                draggable="false"
                @load="index === primaryImageIndex && updateMediaAspectRatio($event)"
              />
            </div>
            <span v-if="index === primaryImageIndex" class="media-primary-badge"><Check :size="12" aria-hidden="true" /> 主图</span>
            <span v-else class="media-result-number">图 {{ index + 1 }}</span>
            <div class="media-result-actions">
              <button
                type="button"
                :title="`下载第 ${index + 1} 张图片`"
                :aria-label="`下载第 ${index + 1} 张图片`"
                :disabled="downloadingImageIndex !== undefined"
                @pointerdown.stop
                @click.stop="emit('downloadImage', index)"
              >
                <LoaderCircle v-if="downloadingImageIndex === index" class="spin" :size="14" aria-hidden="true" />
                <Download v-else :size="14" aria-hidden="true" />
              </button>
              <button
                v-if="index === primaryImageIndex"
                type="button"
                title="收起多图"
                aria-label="收起多图"
                @pointerdown.stop
                @click.stop="toggleGallery"
              ><Minimize2 :size="14" aria-hidden="true" /> 收起</button>
              <button
                v-else
                type="button"
                :title="`将第 ${index + 1} 张设为主图`"
                :aria-label="`将第 ${index + 1} 张设为主图`"
                @pointerdown.stop
                @click.stop="setPrimaryImage(index)"
              ><Pin :size="14" aria-hidden="true" /> 设为主图</button>
            </div>
            <button
              class="media-result-connect"
              type="button"
              :class="{ active: connectionSource && index === connectionImageIndex }"
              :title="`使用第 ${index + 1} 张图片连接`"
              :aria-label="`使用第 ${index + 1} 张图片连接`"
              @pointerdown.stop="startImageConnection($event, index)"
            ><Link2 :size="14" aria-hidden="true" /></button>
          </figure>
        </div>
        <img
          v-else-if="node.type === 'image' && mediaPreviewUrl"
          :src="mediaPreviewUrl"
          :alt="node.prompt || '生成图片'"
          draggable="false"
          @load="updateMediaAspectRatio"
        />
        <video
          v-else-if="node.type === 'video' && mediaPreviewUrl"
          :src="mediaPreviewUrl"
          muted
          controls
          preload="metadata"
          @pointerdown="handlePointerdown"
          @loadedmetadata="updateMediaMetadata"
        />
        <div v-else class="media-empty-state">
          <LoaderCircle v-if="node.status === 'generating' || node.status === 'uploading'" class="spin" :size="34" aria-hidden="true" />
          <button
            v-else-if="node.type === 'image'"
            class="media-upload-button"
            type="button"
            :disabled="!uploadEnabled"
            @pointerdown.stop
            @click.stop="chooseUpload"
          >
            <span class="media-upload-icon"><Upload :size="22" aria-hidden="true" /></span>
            <strong>上传参考图</strong>
            <small>{{ uploadEnabled ? '支持 PNG、JPG、WEBP' : '请先配置服务端存储' }}</small>
          </button>
          <button
            v-else
            class="media-upload-button"
            type="button"
            :disabled="!uploadEnabled"
            @pointerdown.stop
            @click.stop="chooseUpload"
          >
            <span class="media-upload-icon"><Upload :size="22" aria-hidden="true" /></span>
            <strong>上传视频</strong>
            <small>{{ uploadEnabled ? '支持 MP4、WEBM、MOV 等格式' : '请先配置服务端存储' }}</small>
          </button>
        </div>

        <div v-if="mediaDragActive" class="media-drop-overlay" aria-live="polite">
          <Upload :size="24" aria-hidden="true" />
          <strong>松开以上传</strong>
        </div>

        <div v-if="node.type === 'image' && node.references.length" class="media-reference-strip" aria-label="参考图">
          <figure v-for="reference in node.references" :key="reference.id" class="media-reference-thumb">
            <img :src="reference.previewUrl" :alt="reference.name" draggable="false" />
            <LoaderCircle v-if="reference.uploadState === 'uploading'" class="media-reference-loading spin" :size="13" aria-label="上传中" />
            <button
              type="button"
              title="移除参考图"
              :aria-label="`移除参考图 ${reference.name}`"
              @pointerdown.stop
              @click.stop="emit('removeReference', reference.id)"
            ><X :size="12" aria-hidden="true" /></button>
          </figure>
        </div>

      </div>

      <CanvasPromptEditor
        :node="node"
        :node-width="nodeWidth"
        :prompt-width="promptWidth"
        :zoom="zoom"
        :image-enabled="imageEnabled"
        :video-enabled="videoEnabled"
        :upload-enabled="uploadEnabled"
        :image-models="imageModels"
        :video-models="videoModels"
        :video-mode="videoMode"
        :prompt-references="promptReferences"
        :reference-candidates="referenceCandidates"
        :video-analysis-failed="videoAnalysisFailed"
        @select="forwardSelect"
        @update-prompt="forwardPrompt"
        @update-node="forwardNodeUpdate"
        @add-prompt-reference="forwardAddPromptReference"
        @remove-prompt-reference="forwardRemovePromptReference"
        @upload="chooseUpload"
        @generate="generate"
      />

      <input
        ref="fileInput"
        class="visually-hidden"
        type="file"
        :accept="node.type === 'image' ? 'image/png,image/jpeg,image/webp' : 'video/mp4,video/webm,video/quicktime,.m4v,.avi,.mkv'"
        @change="onFileChange"
      />
    </template>

    <CanvasTextNodeBody
      v-else
      :node="node"
      :connection-source="connectionSource"
      :status-label="statusLabel"
      @select="emit('select')"
      @update-content="emit('updateContent', $event)"
      @remove="emit('remove')"
    />
    </div>

    <button
      class="node-port node-port-output"
      type="button"
      :title="hasMultipleImageResults ? `使用主图 ${primaryImageIndex + 1} 开始连接` : '从此节点开始连接'"
      :aria-label="hasMultipleImageResults ? `使用主图 ${primaryImageIndex + 1} 开始连接` : '从此节点开始连接'"
      @pointerdown.stop="emit('startConnection', $event, node.type === 'image' ? primaryImageIndex : undefined)"
      :style="outputPortStyle"
    ><span /></button>
  </article>
</template>
