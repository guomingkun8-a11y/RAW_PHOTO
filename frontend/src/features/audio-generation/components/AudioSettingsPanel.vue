<script setup lang="ts">
import { RefreshCw } from "@lucide/vue";

import DoubaoAudioSettings from "@/features/audio-generation/components/DoubaoAudioSettings.vue";
import GemAudioSettings from "@/features/audio-generation/components/GemAudioSettings.vue";
import {
  DOUBAO_TTS_MODEL,
  GEM_TTS_MODEL,
  type AudioEmotion,
  type AudioGenerationModel,
  type AudioOutputFormat,
  type AudioSpeechRate,
  type AudioVoice,
  type GemReadingMode,
} from "@/features/audio-generation/types/audio-generation";

defineProps<{
  model: AudioGenerationModel;
  voices: AudioVoice[];
  voiceId: string;
  secondaryVoiceId: string;
  speechRate: AudioSpeechRate;
  emotion: AudioEmotion;
  emotionScale: number;
  format: AudioOutputFormat;
  gemReadingMode: GemReadingMode;
  primarySpeaker: string;
  secondarySpeaker: string;
  voicesLoading: boolean;
  voicesError: string;
  disabled: boolean;
}>();

const emit = defineEmits<{
  "update:model": [value: AudioGenerationModel];
  "update:voiceId": [value: string];
  "update:secondaryVoiceId": [value: string];
  "update:speechRate": [value: AudioSpeechRate];
  "update:emotion": [value: AudioEmotion];
  "update:emotionScale": [value: number];
  "update:format": [value: AudioOutputFormat];
  "update:gemReadingMode": [value: GemReadingMode];
  "update:primarySpeaker": [value: string];
  "update:secondarySpeaker": [value: string];
  retryVoices: [];
}>();
</script>

<template>
  <aside class="studio-card audio-generation-panel bg-white p-4 dark:bg-[#171a21] sm:p-5" aria-label="音频参数">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h2 class="text-[15px] font-semibold text-slate-950 dark:text-stone-50">生成参数</h2>
        <p class="mt-1 text-xs text-slate-500 dark:text-stone-400">{{ model }}</p>
      </div>
      <button
        type="button"
        class="studio-button grid size-9 place-items-center rounded-xl border border-black/[0.07] text-slate-500 disabled:cursor-wait disabled:opacity-50 dark:border-white/10 dark:text-stone-300"
        :disabled="voicesLoading || disabled"
        aria-label="刷新音色"
        title="刷新音色"
        @click="emit('retryVoices')"
      >
        <RefreshCw class="size-4" :class="{ 'animate-spin': voicesLoading }" />
      </button>
    </div>

    <fieldset class="mt-5" :disabled="disabled">
      <legend class="mb-2 text-xs font-semibold text-slate-700 dark:text-stone-200">配音模型</legend>
      <div class="grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 dark:bg-white/[0.05]" role="group" aria-label="选择配音模型">
        <button
          type="button"
          class="h-10 rounded-lg px-2 text-xs font-semibold transition-colors"
          :class="model === DOUBAO_TTS_MODEL
            ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white'
            : 'text-slate-500 hover:text-slate-800 dark:text-stone-400 dark:hover:text-stone-100'"
          :aria-pressed="model === DOUBAO_TTS_MODEL"
          data-testid="audio-model-doubao"
          @click="emit('update:model', DOUBAO_TTS_MODEL)"
        >
          豆包 2.0
        </button>
        <button
          type="button"
          class="h-10 rounded-lg px-2 text-xs font-semibold transition-colors"
          :class="model === GEM_TTS_MODEL
            ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white'
            : 'text-slate-500 hover:text-slate-800 dark:text-stone-400 dark:hover:text-stone-100'"
          :aria-pressed="model === GEM_TTS_MODEL"
          data-testid="audio-model-gem"
          @click="emit('update:model', GEM_TTS_MODEL)"
        >
          GEM 3.1
        </button>
      </div>

      <div class="mt-5 border-t border-black/[0.06] pt-5 dark:border-white/10">
        <DoubaoAudioSettings
          v-if="model === DOUBAO_TTS_MODEL"
          :voices="voices"
          :voice-id="voiceId"
          :speech-rate="speechRate"
          :emotion="emotion"
          :emotion-scale="emotionScale"
          :format="format"
          :voices-loading="voicesLoading"
          :voices-error="voicesError"
          :disabled="disabled"
          @update:voice-id="emit('update:voiceId', $event)"
          @update:speech-rate="emit('update:speechRate', $event)"
          @update:emotion="emit('update:emotion', $event)"
          @update:emotion-scale="emit('update:emotionScale', $event)"
          @update:format="emit('update:format', $event)"
          @retry-voices="emit('retryVoices')"
        />

        <GemAudioSettings
          v-else
          :voices="voices"
          :voice-id="voiceId"
          :secondary-voice-id="secondaryVoiceId"
          :reading-mode="gemReadingMode"
          :primary-speaker="primarySpeaker"
          :secondary-speaker="secondarySpeaker"
          :voices-loading="voicesLoading"
          :voices-error="voicesError"
          :disabled="disabled"
          @update:voice-id="emit('update:voiceId', $event)"
          @update:secondary-voice-id="emit('update:secondaryVoiceId', $event)"
          @update:reading-mode="emit('update:gemReadingMode', $event)"
          @update:primary-speaker="emit('update:primarySpeaker', $event)"
          @update:secondary-speaker="emit('update:secondarySpeaker', $event)"
          @retry-voices="emit('retryVoices')"
        />
      </div>
    </fieldset>
  </aside>
</template>
