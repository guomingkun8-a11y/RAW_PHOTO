<script setup lang="ts">
import { computed, ref } from 'vue';
import { Captions, Film, LoaderCircle, Music2, RotateCcw, Settings2, Trash2, Volume2 } from '@lucide/vue';

import type {
  TimelineAudioClip,
  TimelineOutputSettings,
  TimelineSubtitleCue,
  TimelineVideoClip,
  TimelineVisualKeyframe,
} from '@/features/video-timeline/types/timeline';
import { formatTimelineTime } from '@/features/video-timeline/composables/useTimeline';

const props = defineProps<{
  videoClip?: TimelineVideoClip;
  audioClip?: TimelineAudioClip;
  subtitle?: TimelineSubtitleCue;
  output: TimelineOutputSettings;
  duration: number;
  transcribing?: boolean;
}>();

const emit = defineEmits<{
  updateVideo: [updates: Partial<TimelineVideoClip>];
  updateAudio: [updates: Partial<TimelineAudioClip>];
  updateSubtitle: [updates: Partial<TimelineSubtitleCue>];
  updateOutput: [updates: Partial<TimelineOutputSettings>];
  remove: [];
  transcribe: [];
}>();

function numberValue(event: Event) {
  return Number((event.target as HTMLInputElement).value);
}

const keyframePoint = ref<'start' | 'end'>('start');
const visualKeyframe = computed(() => props.videoClip?.keyframes[keyframePoint.value]);

function updateVisualKeyframe(updates: Partial<TimelineVisualKeyframe>) {
  if (!props.videoClip) return;
  emit('updateVideo', {
    keyframes: {
      ...props.videoClip.keyframes,
      [keyframePoint.value]: {
        ...props.videoClip.keyframes[keyframePoint.value],
        ...updates,
      },
    },
  });
}

function resetVisualKeyframe() {
  updateVisualKeyframe({ scale: 1, x: 0, y: 0, rotation: 0, brightness: 0, contrast: 1, saturation: 1 });
}
</script>

<template>
  <aside class="timeline-inspector" aria-label="时间线属性">
    <header class="timeline-inspector-header">
      <span>
        <Film v-if="videoClip" :size="17" aria-hidden="true" />
        <Volume2 v-else-if="audioClip?.kind === 'voiceover'" :size="17" aria-hidden="true" />
        <Music2 v-else-if="audioClip" :size="17" aria-hidden="true" />
        <Captions v-else-if="subtitle" :size="17" aria-hidden="true" />
        <Settings2 v-else :size="17" aria-hidden="true" />
      </span>
      <div>
        <strong v-if="videoClip">片段属性</strong>
        <strong v-else-if="audioClip?.kind === 'voiceover'">旁白属性</strong>
        <strong v-else-if="audioClip">音乐属性</strong>
        <strong v-else-if="subtitle">字幕属性</strong>
        <strong v-else>输出设置</strong>
        <small v-if="videoClip">{{ formatTimelineTime(videoClip.duration) }}</small>
        <small v-else-if="audioClip">{{ formatTimelineTime(audioClip.duration) }}</small>
        <small v-else-if="subtitle">{{ formatTimelineTime(subtitle.start) }} - {{ formatTimelineTime(subtitle.end) }}</small>
        <small v-else>{{ formatTimelineTime(duration) }} 总时长</small>
      </div>
    </header>

    <div v-if="videoClip" class="timeline-inspector-form">
      <label class="timeline-field">
        <span>片段名称</span>
        <input :value="videoClip.title" maxlength="191" @input="emit('updateVideo', { title: ($event.target as HTMLInputElement).value })" />
      </label>
      <label class="timeline-field">
        <span>视频轨道</span>
        <select
          aria-label="视频轨道"
          :value="videoClip.track"
          @change="emit('updateVideo', { track: Number(($event.target as HTMLSelectElement).value) as TimelineVideoClip['track'] })"
        >
          <option :value="0">主视频轨</option>
          <option :value="1">叠加轨 1</option>
          <option :value="2">叠加轨 2（最上层）</option>
        </select>
      </label>
      <div v-if="videoClip.track > 0" class="timeline-field-grid">
        <label class="timeline-field">
          <span>开始时间</span>
          <input
            aria-label="叠加开始时间"
            type="number"
            min="0"
            :max="Math.max(0, duration - 0.1)"
            step="0.1"
            :value="videoClip.timelineStart"
            @input="emit('updateVideo', { timelineStart: numberValue($event) })"
          />
        </label>
        <label class="timeline-field timeline-range-field">
          <span>透明度 <b>{{ Math.round(videoClip.opacity * 100) }}%</b></span>
          <input
            aria-label="叠加透明度"
            type="range"
            min="0"
            max="1"
            step="0.01"
            :value="videoClip.opacity"
            @input="emit('updateVideo', { opacity: numberValue($event) })"
          />
        </label>
      </div>
      <div class="timeline-field-grid">
        <label class="timeline-field">
          <span>素材起点</span>
          <input type="number" min="0" :max="Math.max(0, (videoClip.sourceDuration || 3600) - 0.1)" step="0.1" :value="videoClip.sourceStart" @input="emit('updateVideo', { sourceStart: numberValue($event) })" />
        </label>
        <label class="timeline-field">
          <span>片段时长</span>
          <input type="number" min="0.1" :max="videoClip.sourceDuration ? Math.max(0.1, videoClip.sourceDuration - videoClip.sourceStart) : 600" step="0.1" :value="videoClip.duration" @input="emit('updateVideo', { duration: numberValue($event) })" />
        </label>
      </div>
      <label v-if="videoClip.track === 0" class="timeline-field">
        <span>进入转场</span>
        <select :value="videoClip.transition.type" @change="emit('updateVideo', { transition: { ...videoClip.transition, type: ($event.target as HTMLSelectElement).value as 'cut' | 'fade' } })">
          <option value="cut">直接切换</option>
          <option value="fade">交叉淡化</option>
        </select>
      </label>
      <label class="timeline-check-field">
        <input type="checkbox" :checked="!videoClip.muted" @change="emit('updateVideo', { muted: !($event.target as HTMLInputElement).checked })" />
        <span>保留片段原声</span>
      </label>
      <label v-if="!videoClip.muted" class="timeline-field timeline-range-field">
        <span>原声音量 <b>{{ Math.round(videoClip.volume * 100) }}%</b></span>
        <input type="range" min="0" max="2" step="0.01" :value="videoClip.volume" @input="emit('updateVideo', { volume: numberValue($event) })" />
      </label>
      <label v-if="videoClip.track === 0 && videoClip.transition.type === 'fade'" class="timeline-field">
        <span>转场时长</span>
        <input type="number" min="0.05" max="2" step="0.05" :value="videoClip.transition.duration" @input="emit('updateVideo', { transition: { ...videoClip.transition, duration: numberValue($event) } })" />
      </label>
      <fieldset v-if="visualKeyframe" class="timeline-keyframe-panel">
        <legend>画面关键帧</legend>
        <div class="timeline-keyframe-toolbar">
          <span class="timeline-segmented-control">
            <button type="button" :class="{ active: keyframePoint === 'start' }" @click="keyframePoint = 'start'">片头</button>
            <button type="button" :class="{ active: keyframePoint === 'end' }" @click="keyframePoint = 'end'">片尾</button>
          </span>
          <button class="timeline-icon-button" type="button" title="重置当前关键帧" aria-label="重置当前关键帧" @click="resetVisualKeyframe"><RotateCcw :size="14" /></button>
        </div>
        <label class="timeline-field timeline-range-field">
          <span>缩放 <b>{{ visualKeyframe.scale.toFixed(2) }}x</b></span>
          <input type="range" min="1" max="3" step="0.01" :value="visualKeyframe.scale" @input="updateVisualKeyframe({ scale: numberValue($event) })" />
        </label>
        <div class="timeline-field-grid">
          <label class="timeline-field">
            <span>水平位置</span>
            <input type="number" min="-1" max="1" step="0.05" :value="visualKeyframe.x" @input="updateVisualKeyframe({ x: numberValue($event) })" />
          </label>
          <label class="timeline-field">
            <span>垂直位置</span>
            <input type="number" min="-1" max="1" step="0.05" :value="visualKeyframe.y" @input="updateVisualKeyframe({ y: numberValue($event) })" />
          </label>
        </div>
        <label class="timeline-field timeline-range-field">
          <span>旋转 <b>{{ visualKeyframe.rotation.toFixed(0) }}°</b></span>
          <input type="range" min="-180" max="180" step="1" :value="visualKeyframe.rotation" @input="updateVisualKeyframe({ rotation: numberValue($event) })" />
        </label>
        <label class="timeline-field timeline-range-field">
          <span>亮度 <b>{{ visualKeyframe.brightness.toFixed(2) }}</b></span>
          <input type="range" min="-0.5" max="0.5" step="0.01" :value="visualKeyframe.brightness" @input="updateVisualKeyframe({ brightness: numberValue($event) })" />
        </label>
        <label class="timeline-field timeline-range-field">
          <span>对比度 <b>{{ visualKeyframe.contrast.toFixed(2) }}</b></span>
          <input type="range" min="0.5" max="2" step="0.01" :value="visualKeyframe.contrast" @input="updateVisualKeyframe({ contrast: numberValue($event) })" />
        </label>
        <label class="timeline-field timeline-range-field">
          <span>饱和度 <b>{{ visualKeyframe.saturation.toFixed(2) }}</b></span>
          <input type="range" min="0" max="3" step="0.01" :value="visualKeyframe.saturation" @input="updateVisualKeyframe({ saturation: numberValue($event) })" />
        </label>
      </fieldset>
    </div>

    <div v-else-if="audioClip" class="timeline-inspector-form">
      <label class="timeline-field">
        <span>素材名称</span>
        <input :value="audioClip.title" maxlength="191" @input="emit('updateAudio', { title: ($event.target as HTMLInputElement).value })" />
      </label>
      <div class="timeline-field-grid">
        <label class="timeline-field">
          <span>开始时间</span>
          <input type="number" min="0" max="7200" step="0.1" :value="audioClip.start" @input="emit('updateAudio', { start: numberValue($event) })" />
        </label>
        <label class="timeline-field">
          <span>播放时长</span>
          <input type="number" min="0.1" max="7200" step="0.1" :value="audioClip.duration" @input="emit('updateAudio', { duration: numberValue($event) })" />
        </label>
      </div>
      <label class="timeline-field timeline-range-field">
        <span>音量 <b>{{ Math.round(audioClip.volume * 100) }}%</b></span>
        <input type="range" min="0" max="2" step="0.01" :value="audioClip.volume" @input="emit('updateAudio', { volume: numberValue($event) })" />
      </label>
      <div class="timeline-field-grid">
        <label class="timeline-field">
          <span>淡入</span>
          <input type="number" min="0" max="10" step="0.1" :value="audioClip.fadeIn" @input="emit('updateAudio', { fadeIn: numberValue($event) })" />
        </label>
        <label class="timeline-field">
          <span>淡出</span>
          <input type="number" min="0" max="10" step="0.1" :value="audioClip.fadeOut" @input="emit('updateAudio', { fadeOut: numberValue($event) })" />
        </label>
      </div>
      <label v-if="audioClip.kind === 'music'" class="timeline-check-field">
        <input type="checkbox" :checked="audioClip.loop" @change="emit('updateAudio', { loop: ($event.target as HTMLInputElement).checked })" />
        <span>循环至片尾</span>
      </label>
      <label v-if="audioClip.kind === 'voiceover'" class="timeline-field">
        <span>旁白文案</span>
        <textarea rows="5" maxlength="20000" :value="audioClip.script" @input="emit('updateAudio', { script: ($event.target as HTMLTextAreaElement).value })" />
      </label>
      <button
        v-if="audioClip.kind === 'voiceover' && audioClip.storageRel"
        class="timeline-secondary-button timeline-full-button"
        type="button"
        :disabled="transcribing"
        @click="emit('transcribe')"
      >
        <LoaderCircle v-if="transcribing" class="timeline-spin" :size="15" />
        <Captions v-else :size="15" />
        {{ transcribing ? '正在识别语音' : '自动生成字幕' }}
      </button>
    </div>

    <div v-else-if="subtitle" class="timeline-inspector-form">
      <label class="timeline-field">
        <span>字幕内容</span>
        <textarea rows="5" maxlength="1000" :value="subtitle.text" @input="emit('updateSubtitle', { text: ($event.target as HTMLTextAreaElement).value })" />
      </label>
      <div class="timeline-field-grid">
        <label class="timeline-field">
          <span>开始时间</span>
          <input type="number" min="0" max="7200" step="0.1" :value="subtitle.start" @input="emit('updateSubtitle', { start: numberValue($event) })" />
        </label>
        <label class="timeline-field">
          <span>结束时间</span>
          <input type="number" min="0.1" max="7200" step="0.1" :value="subtitle.end" @input="emit('updateSubtitle', { end: numberValue($event) })" />
        </label>
      </div>
    </div>

    <div v-else class="timeline-inspector-form">
      <label class="timeline-field">
        <span>画面比例</span>
        <select :value="output.aspectRatio" @change="emit('updateOutput', { aspectRatio: ($event.target as HTMLSelectElement).value as TimelineOutputSettings['aspectRatio'] })">
          <option value="16:9">16:9 横屏</option>
          <option value="9:16">9:16 竖屏</option>
          <option value="1:1">1:1 方形</option>
        </select>
      </label>
      <div class="timeline-field-grid">
        <label class="timeline-field">
          <span>分辨率</span>
          <select :value="output.resolution" @change="emit('updateOutput', { resolution: ($event.target as HTMLSelectElement).value as TimelineOutputSettings['resolution'] })">
            <option value="720p">720P</option>
            <option value="1080p">1080P</option>
          </select>
        </label>
        <label class="timeline-field">
          <span>帧率</span>
          <select :value="output.fps" @change="emit('updateOutput', { fps: Number(($event.target as HTMLSelectElement).value) as TimelineOutputSettings['fps'] })">
            <option :value="24">24 fps</option>
            <option :value="25">25 fps</option>
            <option :value="30">30 fps</option>
          </select>
        </label>
      </div>
      <label class="timeline-check-field">
        <input type="checkbox" :checked="output.burnSubtitles" @change="emit('updateOutput', { burnSubtitles: ($event.target as HTMLInputElement).checked })" />
        <span>字幕嵌入视频</span>
      </label>
      <label class="timeline-field">
        <span>画面填充</span>
        <select :value="output.fit" @change="emit('updateOutput', { fit: ($event.target as HTMLSelectElement).value as TimelineOutputSettings['fit'] })">
          <option value="contain">完整显示</option>
          <option value="cover">铺满裁切</option>
        </select>
      </label>
      <label v-if="output.fit === 'contain'" class="timeline-field">
        <span>留白背景</span>
        <input type="color" :value="output.backgroundColor" @input="emit('updateOutput', { backgroundColor: ($event.target as HTMLInputElement).value })" />
      </label>
    </div>

    <footer v-if="videoClip || audioClip || subtitle" class="timeline-inspector-footer">
      <button class="timeline-danger-button" type="button" @click="emit('remove')">
        <Trash2 :size="15" aria-hidden="true" />
        删除当前素材
      </button>
    </footer>
  </aside>
</template>
