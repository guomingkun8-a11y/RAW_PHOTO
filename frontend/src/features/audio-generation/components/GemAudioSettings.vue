<script setup lang="ts">
import { computed } from "vue";

import AudioVoicePicker from "@/features/audio-generation/components/AudioVoicePicker.vue";
import type { AudioVoice, GemReadingMode } from "@/features/audio-generation/types/audio-generation";

const props = defineProps<{
  voices: AudioVoice[];
  voiceId: string;
  secondaryVoiceId: string;
  readingMode: GemReadingMode;
  primarySpeaker: string;
  secondarySpeaker: string;
  voicesLoading: boolean;
  voicesError: string;
  disabled: boolean;
}>();

const emit = defineEmits<{
  "update:voiceId": [value: string];
  "update:secondaryVoiceId": [value: string];
  "update:readingMode": [value: GemReadingMode];
  "update:primarySpeaker": [value: string];
  "update:secondarySpeaker": [value: string];
  retryVoices: [];
}>();

const duplicateSpeakerNames = computed(() => {
  const first = props.primarySpeaker.trim().toLocaleLowerCase();
  const second = props.secondarySpeaker.trim().toLocaleLowerCase();
  return props.readingMode === "dialogue" && Boolean(first) && first === second;
});

const inputClass = "mt-2 h-10 w-full rounded-xl border border-black/[0.07] bg-slate-50 px-3 text-sm font-medium text-slate-900 outline-none transition focus:border-[#4F7CFF]/45 focus:ring-2 focus:ring-[#4F7CFF]/10 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-100";
</script>

<template>
  <div class="space-y-5">
    <div>
      <label class="mb-2 block text-xs font-semibold text-slate-700 dark:text-stone-200">朗读模式</label>
      <div class="grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 dark:bg-white/[0.05]" role="group" aria-label="选择朗读模式">
        <button
          type="button"
          class="h-9 rounded-lg text-xs font-semibold transition-colors"
          :class="readingMode === 'single'
            ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white'
            : 'text-slate-500 hover:text-slate-800 dark:text-stone-400 dark:hover:text-stone-100'"
          :aria-pressed="readingMode === 'single'"
          data-testid="gem-reading-single"
          @click="emit('update:readingMode', 'single')"
        >
          单人朗读
        </button>
        <button
          type="button"
          class="h-9 rounded-lg text-xs font-semibold transition-colors"
          :class="readingMode === 'dialogue'
            ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white'
            : 'text-slate-500 hover:text-slate-800 dark:text-stone-400 dark:hover:text-stone-100'"
          :aria-pressed="readingMode === 'dialogue'"
          data-testid="gem-reading-dialogue"
          @click="emit('update:readingMode', 'dialogue')"
        >
          双人对话
        </button>
      </div>
    </div>

    <AudioVoicePicker
      v-if="readingMode === 'single'"
      :model-value="voiceId"
      :voices="voices"
      :loading="voicesLoading"
      :error="voicesError"
      :disabled="disabled"
      @update:model-value="emit('update:voiceId', $event)"
      @retry="emit('retryVoices')"
    />

    <template v-else>
      <section aria-label="角色一设置">
        <div class="text-xs font-semibold text-slate-950 dark:text-stone-100">角色 1</div>
        <label class="mt-3 block text-xs font-semibold text-slate-700 dark:text-stone-200">
          角色名称
          <input
            :class="inputClass"
            type="text"
            maxlength="40"
            :value="primarySpeaker"
            :disabled="disabled"
            aria-label="角色一名称"
            @input="emit('update:primarySpeaker', ($event.target as HTMLInputElement).value)"
          />
        </label>
        <div class="mt-3">
          <AudioVoicePicker
            :model-value="voiceId"
            :voices="voices"
            label="角色 1 音色"
            aria-label="选择角色一音色"
            :loading="voicesLoading"
            :error="voicesError"
            :disabled="disabled"
            @update:model-value="emit('update:voiceId', $event)"
            @retry="emit('retryVoices')"
          />
        </div>
      </section>

      <section class="border-t border-black/[0.06] pt-5 dark:border-white/10" aria-label="角色二设置">
        <div class="text-xs font-semibold text-slate-950 dark:text-stone-100">角色 2</div>
        <label class="mt-3 block text-xs font-semibold text-slate-700 dark:text-stone-200">
          角色名称
          <input
            :class="inputClass"
            type="text"
            maxlength="40"
            :value="secondarySpeaker"
            :disabled="disabled"
            aria-label="角色二名称"
            @input="emit('update:secondarySpeaker', ($event.target as HTMLInputElement).value)"
          />
        </label>
        <div class="mt-3">
          <AudioVoicePicker
            :model-value="secondaryVoiceId"
            :voices="voices"
            label="角色 2 音色"
            aria-label="选择角色二音色"
            :loading="voicesLoading"
            :error="voicesError"
            :disabled="disabled"
            :show-count="false"
            @update:model-value="emit('update:secondaryVoiceId', $event)"
            @retry="emit('retryVoices')"
          />
        </div>
      </section>

      <p v-if="duplicateSpeakerNames" class="text-xs font-medium text-rose-600 dark:text-rose-300" role="alert">
        两个角色名称不能相同
      </p>
    </template>
  </div>
</template>
