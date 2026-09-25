<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { LoaderCircle, Mic2, Sparkles, X } from '@lucide/vue';

import type {
  TimelineCapabilities,
  TimelineVoiceEmotion,
  TimelineVoiceOption,
  TimelineVoiceoverInput,
} from '@/features/video-timeline/types/timeline';

const props = defineProps<{
  capabilities?: TimelineCapabilities;
  loading?: boolean;
  generating?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  generate: [input: TimelineVoiceoverInput];
}>();

const defaultVoice = 'zh_female_vv_uranus_bigtts';
const emotionLabels: Record<TimelineVoiceEmotion, string> = {
  auto: '自动',
  happy: '开心',
  sad: '悲伤',
  angry: '愤怒',
  surprised: '惊讶',
  fear: '恐惧',
  hate: '厌恶',
  neutral: '中性',
  chat: '聊天',
};
const text = ref('');
const voice = ref(defaultVoice);
const speechRate = ref(0);
const emotion = ref<TimelineVoiceEmotion>('auto');
const emotionScale = ref(4);
const createCaptions = ref(true);
const voiceOptions = computed<TimelineVoiceOption[]>(() => {
  const configured = props.capabilities?.voiceover.voice_options;
  if (configured?.length) return configured;
  return (props.capabilities?.voiceover.voices || [defaultVoice]).map((id) => ({ id, name: id }));
});
const speechRateConfig = computed(() => props.capabilities?.voiceover.speech_rate || { min: -50, max: 100, default: 0 });
const emotionScaleConfig = computed(() => props.capabilities?.voiceover.emotion_scale || { min: 1, max: 5, default: 4 });
const emotions = computed(() => props.capabilities?.voiceover.emotions || Object.keys(emotionLabels) as TimelineVoiceEmotion[]);
const maxChars = computed(() => props.capabilities?.voiceover.max_chars || 5000);
const enabled = computed(() => props.capabilities?.voiceover.enabled !== false);

watch(voiceOptions, (values) => {
  const preferred = props.capabilities?.voiceover.default_voice || defaultVoice;
  if (!values.some((item) => item.id === voice.value)) {
    voice.value = values.find((item) => item.id === preferred)?.id || values[0]?.id || defaultVoice;
  }
}, { immediate: true });

function voiceLabel(item: TimelineVoiceOption) {
  if (item.name === item.id) return item.name;
  return `${item.name} · ${item.id}`;
}

function submit() {
  const script = text.value.trim();
  if (!script || props.generating || !enabled.value) return;
  emit('generate', {
    text: script,
    voice: voice.value,
    speechRate: speechRate.value,
    emotion: emotion.value,
    emotionScale: emotionScale.value,
    createCaptions: createCaptions.value,
  });
}
</script>

<template>
  <aside class="timeline-ai-panel" aria-label="AI 配音">
    <header class="timeline-inspector-header">
      <span><Mic2 :size="17" aria-hidden="true" /></span>
      <div>
        <strong>AI 配音</strong>
        <small>{{ capabilities?.voiceover.model || '语音模型' }}</small>
      </div>
      <button class="timeline-icon-button" type="button" title="关闭 AI 配音" aria-label="关闭 AI 配音" @click="emit('close')"><X :size="16" /></button>
    </header>

    <div class="timeline-inspector-form">
      <div v-if="loading" class="timeline-inline-state" aria-live="polite">
        <LoaderCircle class="timeline-spin" :size="16" /> 正在读取语音能力
      </div>
      <div v-else-if="!enabled" class="timeline-inline-state timeline-inline-state-error">
        当前未配置可用的 OpenAI Relay 语音接口。
      </div>
      <template v-else>
        <label class="timeline-field">
          <span>旁白文案 <b>{{ text.length }}/{{ maxChars }}</b></span>
          <textarea v-model="text" rows="8" :maxlength="maxChars" placeholder="输入需要朗读的旁白文案" />
        </label>
        <label class="timeline-field">
          <span>音色</span>
          <select v-model="voice" aria-label="音色">
            <option v-for="item in voiceOptions" :key="item.id" :value="item.id">{{ voiceLabel(item) }}</option>
          </select>
        </label>
        <label class="timeline-field timeline-range-field">
          <span>语速 <b>{{ speechRate > 0 ? `+${speechRate}` : speechRate }}</b></span>
          <input
            v-model.number="speechRate"
            type="range"
            aria-label="语速"
            :min="speechRateConfig.min"
            :max="speechRateConfig.max"
            step="1"
          />
        </label>
        <div class="timeline-field-grid">
          <label class="timeline-field">
            <span>情绪</span>
            <select v-model="emotion" aria-label="情绪">
              <option v-for="item in emotions" :key="item" :value="item">{{ emotionLabels[item] || item }}</option>
            </select>
          </label>
          <label class="timeline-field">
            <span>情绪强度</span>
            <input
              v-model.number="emotionScale"
              type="number"
              aria-label="情绪强度"
              :min="emotionScaleConfig.min"
              :max="emotionScaleConfig.max"
              step="1"
              :disabled="emotion === 'auto'"
            />
          </label>
        </div>
        <label class="timeline-check-field">
          <input v-model="createCaptions" type="checkbox" />
          <span>同步创建字幕</span>
        </label>
      </template>
    </div>

    <footer class="timeline-inspector-footer">
      <button class="timeline-primary-button timeline-full-button" type="button" :disabled="loading || generating || !enabled || !text.trim()" @click="submit">
        <LoaderCircle v-if="generating" class="timeline-spin" :size="15" />
        <Sparkles v-else :size="15" />
        {{ generating ? '正在生成配音' : '生成并加入时间线' }}
      </button>
    </footer>
  </aside>
</template>
