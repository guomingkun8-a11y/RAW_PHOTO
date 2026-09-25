<script setup lang="ts">
import ComposerSelect from "@/components/ComposerSelect.vue";
import AudioVoicePicker from "@/features/audio-generation/components/AudioVoicePicker.vue";
import type {
  AudioEmotion,
  AudioOutputFormat,
  AudioSpeechRate,
  AudioVoice,
} from "@/features/audio-generation/types/audio-generation";

defineProps<{
  voices: AudioVoice[];
  voiceId: string;
  speechRate: AudioSpeechRate;
  emotion: AudioEmotion;
  emotionScale: number;
  format: AudioOutputFormat;
  voicesLoading: boolean;
  voicesError: string;
  disabled: boolean;
}>();

const emit = defineEmits<{
  "update:voiceId": [value: string];
  "update:speechRate": [value: AudioSpeechRate];
  "update:emotion": [value: AudioEmotion];
  "update:emotionScale": [value: number];
  "update:format": [value: AudioOutputFormat];
  retryVoices: [];
}>();

const rateOptions: Array<{ value: AudioSpeechRate; label: string; title: string }> = [
  { value: -50, label: "0.5x", title: "极慢" },
  { value: -25, label: "0.75x", title: "较慢" },
  { value: 0, label: "1.0x", title: "正常" },
  { value: 25, label: "1.25x", title: "较快" },
  { value: 50, label: "1.5x", title: "快速" },
  { value: 100, label: "2.0x", title: "极快" },
];

const emotionOptions: Array<{ value: AudioEmotion; label: string }> = [
  { value: "auto", label: "自动" },
  { value: "happy", label: "开心" },
  { value: "sad", label: "悲伤" },
  { value: "angry", label: "愤怒" },
  { value: "fearful", label: "害怕" },
  { value: "surprised", label: "惊讶" },
  { value: "calm", label: "平静" },
];

const formatOptions: Array<{ value: AudioOutputFormat; label: string }> = [
  { value: "mp3", label: "MP3" },
  { value: "wav", label: "WAV" },
  { value: "pcm", label: "PCM" },
  { value: "ogg_opus", label: "OGG" },
];
</script>

<template>
  <div class="space-y-5">
    <AudioVoicePicker
      :model-value="voiceId"
      :voices="voices"
      :loading="voicesLoading"
      :error="voicesError"
      :disabled="disabled"
      @update:model-value="emit('update:voiceId', $event)"
      @retry="emit('retryVoices')"
    />

    <div>
      <label class="mb-2 block text-xs font-semibold text-slate-700 dark:text-stone-200">语速</label>
      <div class="grid grid-cols-3 gap-1.5" role="group" aria-label="选择语速">
        <button
          v-for="option in rateOptions"
          :key="option.value"
          type="button"
          class="studio-button h-10 rounded-xl border text-xs font-semibold tabular-nums transition"
          :class="speechRate === option.value
            ? 'border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db5ff]'
            : 'border-black/[0.06] bg-slate-50 text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-300 dark:hover:bg-white/[0.07]'"
          :aria-pressed="speechRate === option.value"
          :title="option.title"
          @click="emit('update:speechRate', option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <ComposerSelect
      :model-value="emotion"
      label="情感"
      aria-label="选择情感"
      variant="wide"
      :options="emotionOptions"
      @update:model-value="emit('update:emotion', $event as AudioEmotion)"
    />

    <label class="block" :class="{ 'opacity-45': emotion === 'auto' }">
      <span class="mb-2 flex items-center justify-between text-xs font-semibold text-slate-700 dark:text-stone-200">
        <span>情感强度</span>
        <span class="tabular-nums">{{ emotion === 'auto' ? '自动' : emotionScale }}</span>
      </span>
      <input
        class="audio-strength-slider w-full accent-[#4F7CFF]"
        type="range"
        min="1"
        max="5"
        step="1"
        :value="emotionScale"
        :disabled="emotion === 'auto' || disabled"
        aria-label="情感强度"
        @input="emit('update:emotionScale', Number(($event.target as HTMLInputElement).value))"
      />
    </label>

    <div>
      <label class="mb-2 block text-xs font-semibold text-slate-700 dark:text-stone-200">输出格式</label>
      <div class="grid grid-cols-4 gap-1.5" role="group" aria-label="选择输出格式">
        <button
          v-for="option in formatOptions"
          :key="option.value"
          type="button"
          class="studio-button h-10 rounded-xl border text-[11px] font-semibold transition"
          :class="format === option.value
            ? 'border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db5ff]'
            : 'border-black/[0.06] bg-slate-50 text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-300 dark:hover:bg-white/[0.07]'"
          :aria-pressed="format === option.value"
          @click="emit('update:format', option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.audio-strength-slider {
  height: 20px;
  cursor: pointer;
}

.audio-strength-slider:disabled {
  cursor: not-allowed;
}
</style>
