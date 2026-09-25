<script setup lang="ts">
import { computed, ref } from 'vue';
import { ArrowUp, Clock3, Image as ImageIcon, Link2, LoaderCircle, Upload, Video, X } from '@lucide/vue';
import type { VideoGenerationModelSpec } from '@/lib/api';
import { BUILTIN_IMAGE_MODELS, formatImageModel } from '@/lib/image-models';
import {
  formatVideoDuration,
  formatVideoModelOption,
  videoFrameRole,
  videoModelOptionLabel,
  videoModelUsesFirstLastFrames,
} from '@/lib/video-models';
import CanvasSelect from '@/features/infinite-canvas/components/CanvasSelect.vue';
import {
  CANVAS_IMAGE_ASPECT_RATIOS,
  CANVAS_IMAGE_COUNTS,
  CANVAS_IMAGE_QUALITIES,
  CANVAS_IMAGE_SIZES,
  CANVAS_IMAGE_THINKING_LEVELS,
  type CanvasMediaReference,
  type CanvasNode,
} from '@/features/infinite-canvas/types';

const props = defineProps<{
  node: CanvasNode;
  nodeWidth: number;
  promptWidth: number;
  zoom: number;
  imageEnabled: boolean;
  videoEnabled: boolean;
  uploadEnabled: boolean;
  imageModels: string[];
  videoModels: VideoGenerationModelSpec[];
  videoMode: 'text_to_video' | 'image_to_video';
  promptReferences: CanvasMediaReference[];
  referenceCandidates: CanvasMediaReference[];
  videoAnalysisFailed: boolean;
}>();

const emit = defineEmits<{
  select: [];
  updatePrompt: [value: string];
  updateNode: [payload: { key: keyof CanvasNode; value: CanvasNode[keyof CanvasNode] }];
  addPromptReference: [sourceNodeId: string];
  removePromptReference: [sourceNodeId: string];
  upload: [];
  generate: [];
}>();

const promptInput = ref<HTMLTextAreaElement>();
const availableImageModels = computed(() => props.imageModels.length ? props.imageModels : [...BUILTIN_IMAGE_MODELS]);
const defaultImageModel = computed(() => availableImageModels.value.includes('gpt-image-2')
  ? 'gpt-image-2'
  : availableImageModels.value[0] || BUILTIN_IMAGE_MODELS[0]);
const selectedImageModel = computed(() => availableImageModels.value.includes(props.node.model || '')
  ? props.node.model
  : defaultImageModel.value);
const compatibleVideoModels = computed(() => props.videoModels.filter((model) => model.modes.includes(props.videoMode)));
const textVideoModels = computed(() => props.videoModels.filter((model) => model.modes.includes('text_to_video')));
const imageVideoModels = computed(() => props.videoModels.filter((model) => model.modes.includes('image_to_video')));
const selectedVideoModel = computed(() => compatibleVideoModels.value.find((model) => model.id === props.node.model)
  || compatibleVideoModels.value[0]
  || props.videoModels[0]);
const referenceSelectOptions = computed(() => [
  ...props.referenceCandidates.filter((item) => item.type === 'image').map((reference) => ({
    value: reference.nodeId,
    label: reference.label,
    group: '图片',
  })),
  ...props.referenceCandidates.filter((item) => item.type === 'video').map((reference) => ({
    value: reference.nodeId,
    label: reference.label,
    group: '视频',
  })),
]);
const imageModelSelectOptions = computed(() => availableImageModels.value.map((model) => ({
  value: model,
  label: formatImageModel(model),
})));
const videoModelSelectOptions = computed(() => {
  const seen = new Set<string>();
  return [
    ...textVideoModels.value.map((model) => ({ value: model.id, label: model.label, group: '文生视频' })),
    ...imageVideoModels.value.map((model) => ({ value: model.id, label: model.label, group: '图生视频' })),
  ].filter((option) => {
    if (seen.has(option.value)) return false;
    seen.add(option.value);
    return true;
  });
});
const displayedPromptReferences = computed(() => {
  let imageIndex = 0;
  return props.promptReferences.map((reference) => {
    if (props.node.type !== 'video' || reference.type !== 'image') return reference;
    const role = videoFrameRole(selectedVideoModel.value, imageIndex);
    imageIndex += 1;
    return role ? { ...reference, label: `${role} · ${reference.label}`, title: `${role}：${reference.title}` } : reference;
  });
});
const selectedVideoAspectRatio = computed(() => {
  const spec = selectedVideoModel.value;
  return spec && spec.aspect_ratios.includes(props.node.aspectRatio) ? props.node.aspectRatio : spec?.aspect_ratios[0] || props.node.aspectRatio;
});
const selectedVideoDuration = computed(() => {
  const spec = selectedVideoModel.value;
  return spec && spec.durations.includes(props.node.duration) ? props.node.duration : spec?.default_duration || props.node.duration;
});
const selectedVideoOption = computed(() => {
  const spec = selectedVideoModel.value;
  return spec && spec.options.includes(props.node.quality) ? props.node.quality : spec?.default_option || props.node.quality;
});
const aspectRatioSelectOptions = computed(() => (
  props.node.type === 'video' ? selectedVideoModel.value?.aspect_ratios || [] : CANVAS_IMAGE_ASPECT_RATIOS
).map((aspectRatio) => ({ value: aspectRatio, label: aspectRatio === 'adaptive' ? '自适应' : aspectRatio })));
const imageCountSelectOptions = CANVAS_IMAGE_COUNTS.map((value) => ({ value: String(value), label: `${value}张` }));
const imageSizeSelectOptions = CANVAS_IMAGE_SIZES.map((value) => ({ value, label: value }));
const imageQualitySelectOptions = CANVAS_IMAGE_QUALITIES.map((value) => ({ value, label: value }));
const imageThinkingSelectOptions = CANVAS_IMAGE_THINKING_LEVELS.map((value) => ({ value, label: value }));
const videoDurationSelectOptions = computed(() => (selectedVideoModel.value?.durations || [])
  .map((value) => ({ value: String(value), label: formatVideoDuration(value) })));
const videoOptionSelectOptions = computed(() => (selectedVideoModel.value?.options || [])
  .map((value) => ({ value, label: selectedVideoModel.value ? formatVideoModelOption(value, selectedVideoModel.value) : value })));
const videoOptionLabel = computed(() => selectedVideoModel.value ? videoModelOptionLabel(selectedVideoModel.value) : '参数');
const promptPlaceholder = computed(() => {
  if (props.node.type === 'image') return '描述主体、环境、光线和画面细节...';
  return videoModelUsesFirstLastFrames(selectedVideoModel.value)
    ? '描述首帧到尾帧之间的动作、镜头运动和节奏...'
    : '描述动作、镜头运动、节奏和氛围...';
});
const canGenerate = computed(() => props.node.type === 'image' ? props.imageEnabled : props.videoEnabled);
const editorStyle = computed(() => {
  const inverseScale = 1 / Math.max(props.zoom, 0.01);
  return { left: `${(props.nodeWidth / 2) - ((props.promptWidth * inverseScale) / 2)}px` };
});

function updateField(key: keyof CanvasNode, value: CanvasNode[keyof CanvasNode]) {
  emit('updateNode', { key, value });
}

function selectVideoModel(modelId: string) {
  const spec = props.videoModels.find((item) => item.id === modelId);
  if (!spec) return;
  updateField('model', spec.id);
  updateField('aspectRatio', spec.aspect_ratios[0] || '16:9');
  updateField('duration', spec.default_duration ?? spec.durations[0] ?? 5);
  updateField('quality', spec.default_option || spec.options[0] || '');
}

function focusPrompt() {
  emit('select');
  promptInput.value?.focus();
}
</script>

<template>
  <section class="media-prompt-editor" :class="{ 'has-error': node.error || videoAnalysisFailed }" :style="editorStyle" @wheel.stop>
    <div class="media-prompt-shortcuts">
      <button :title="node.type === 'image' ? '添加参考图' : '上传视频'" type="button" :disabled="!uploadEnabled" @pointerdown.stop @click.stop="emit('upload')">
        <span v-if="node.type === 'image'">+</span><Upload v-else :size="13" aria-hidden="true" />{{ node.type === 'image' ? '参考' : '上传' }}
      </button>
      <CanvasSelect
        class="media-prompt-reference-picker"
        variant="action"
        :model-value="''"
        :options="referenceSelectOptions"
        label="引用"
        :aria-label="`${node.title}引用素材`"
        :disabled="!referenceCandidates.length"
        :title="referenceCandidates.length ? '引用画布中的图片或视频' : '暂无可引用的图片或视频节点'"
        @update:model-value="$event && emit('addPromptReference', $event)"
      >
        <template #prefix><Link2 :size="13" aria-hidden="true" /></template>
      </CanvasSelect>
      <span class="prompt-counter">{{ node.prompt.length }}/2000</span>
    </div>
    <div class="media-prompt-input" :class="{ 'has-references': displayedPromptReferences.length }" @pointerdown.stop @click="focusPrompt">
      <span
        v-for="reference in displayedPromptReferences"
        :key="reference.nodeId"
        class="media-prompt-reference-chip"
        :class="[`is-${reference.type}`, { 'is-indirect': !reference.direct }]"
        :title="reference.direct ? reference.title : `${reference.title}（通过上游流程引用）`"
      >
        <span class="media-prompt-reference-preview" aria-hidden="true">
          <img v-if="reference.type === 'image' && reference.previewUrl" :src="reference.previewUrl" alt="" draggable="false" />
          <ImageIcon v-else-if="reference.type === 'image'" :size="12" />
          <Video v-else :size="12" />
        </span>
        <span>{{ reference.label }}</span>
        <button v-if="reference.direct" type="button" :title="`移除引用 ${reference.label}`" :aria-label="`移除引用 ${reference.label}`" @pointerdown.stop @click.stop="emit('removePromptReference', reference.nodeId)"><X :size="11" aria-hidden="true" /></button>
      </span>
      <textarea
        ref="promptInput"
        class="media-prompt-textarea"
        :value="node.prompt"
        :placeholder="promptPlaceholder"
        :aria-label="`${node.title}提示词`"
        maxlength="2000"
        rows="3"
        @focus="emit('select')"
        @pointerdown.stop
        @click.stop
        @dblclick.stop
        @input="emit('updatePrompt', ($event.target as HTMLTextAreaElement).value)"
      />
    </div>
    <div class="media-prompt-toolbar">
      <div class="media-toolbar-group">
        <CanvasSelect v-if="node.type === 'image'" class="media-toolbar-select media-toolbar-model" :model-value="selectedImageModel || ''" :options="imageModelSelectOptions" label="模型" :aria-label="`${node.title}模型`" title="gpt-image-2 会按服务端规则请求 tt-image-2" @update:model-value="updateField('model', $event)" />
        <CanvasSelect v-else class="media-toolbar-select media-toolbar-model" :model-value="selectedVideoModel?.id || ''" :options="videoModelSelectOptions" label="模型" :aria-label="`${node.title}模型`" @update:model-value="selectVideoModel" />
        <CanvasSelect class="media-toolbar-select" :model-value="node.type === 'video' ? selectedVideoAspectRatio : node.aspectRatio" :options="aspectRatioSelectOptions" label="画幅" :aria-label="`${node.title}画幅`" @update:model-value="updateField('aspectRatio', $event)" />
        <CanvasSelect v-if="node.type === 'image'" class="media-toolbar-select" :model-value="String(node.imageCount)" :options="imageCountSelectOptions" label="数量" :aria-label="`${node.title}生成数量`" @update:model-value="updateField('imageCount', Number($event))" />
        <CanvasSelect v-if="node.type === 'image'" class="media-toolbar-select" :model-value="node.imageSize" :options="imageSizeSelectOptions" label="分辨率" :aria-label="`${node.title}分辨率`" @update:model-value="updateField('imageSize', $event)" />
        <CanvasSelect v-if="node.type === 'image'" class="media-toolbar-select" :model-value="node.quality" :options="imageQualitySelectOptions" label="质量" :aria-label="`${node.title}质量`" @update:model-value="updateField('quality', $event)" />
        <CanvasSelect v-if="node.type === 'image'" class="media-toolbar-select media-toolbar-thinking" :model-value="node.thinkingLevel" :options="imageThinkingSelectOptions" label="思考" :aria-label="`${node.title}思考等级`" @update:model-value="updateField('thinkingLevel', $event)" />
        <CanvasSelect v-else class="media-toolbar-select media-toolbar-duration" :model-value="String(selectedVideoDuration)" :options="videoDurationSelectOptions" label="时长" :aria-label="`${node.title}时长`" @update:model-value="updateField('duration', $event === 'auto' ? 'auto' : Number($event))">
          <template #prefix><Clock3 :size="12" aria-hidden="true" /></template>
        </CanvasSelect>
        <CanvasSelect v-if="node.type === 'video'" class="media-toolbar-select" :model-value="selectedVideoOption || ''" :options="videoOptionSelectOptions" :label="videoOptionLabel" :aria-label="`${node.title}${videoOptionLabel}`" @update:model-value="updateField('quality', $event)" />
      </div>
      <button class="media-generate-button" type="button" :disabled="!canGenerate || node.status === 'generating' || node.status === 'uploading'" :title="node.status === 'generating' ? '生成中' : canGenerate ? `生成${node.type === 'image' ? '图片' : '视频'}` : '请先配置服务端模型'" :aria-label="node.status === 'generating' ? '生成中' : `生成${node.type === 'image' ? '图片' : '视频'}`" @pointerdown.stop @click.stop="emit('generate')">
        <LoaderCircle v-if="node.status === 'generating'" class="spin" :size="18" aria-hidden="true" />
        <ArrowUp v-else :size="19" aria-hidden="true" />
      </button>
    </div>
    <p v-if="node.error" class="media-node-error" :title="node.error">{{ node.error }}</p>
    <p v-else-if="videoAnalysisFailed" class="media-node-error" :title="node.videoAnalysisError">{{ node.videoAnalysisError || '视频解析失败，请重试。' }}</p>
    <p v-else-if="!canGenerate" class="media-node-warning">服务端尚未配置{{ node.type === 'image' ? '图片' : '视频' }}模型</p>
  </section>
</template>
