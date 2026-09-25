<script setup lang="ts">
import { Play, Square } from "@lucide/vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import ComposerSelect from "@/components/ComposerSelect.vue";
import type { AudioVoice } from "@/features/audio-generation/types/audio-generation";

const props = withDefaults(defineProps<{
  modelValue: string;
  voices: AudioVoice[];
  label?: string;
  ariaLabel?: string;
  loading?: boolean;
  error?: string;
  disabled?: boolean;
  showCount?: boolean;
}>(), {
  label: "音色",
  ariaLabel: "选择音色",
  loading: false,
  error: "",
  disabled: false,
  showCount: true,
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  retry: [];
}>();

const previewing = ref(false);
let previewAudio: HTMLAudioElement | null = null;

const selectedVoice = computed(() => props.voices.find((voice) => voice.voice_id === props.modelValue));
const voiceOptions = computed(() => props.voices.map((voice) => ({
  value: voice.voice_id,
  label: [voice.name, voice.language, voice.gender].filter(Boolean).join(" · "),
})));

function stopPreview() {
  previewAudio?.pause();
  previewAudio = null;
  previewing.value = false;
}

async function togglePreview() {
  if (previewing.value) {
    stopPreview();
    return;
  }
  const source = selectedVoice.value?.demo_audio;
  if (!source) return;
  stopPreview();
  const audio = new Audio(source);
  previewAudio = audio;
  previewing.value = true;
  audio.addEventListener("ended", stopPreview, { once: true });
  audio.addEventListener("error", stopPreview, { once: true });
  try {
    await audio.play();
  } catch {
    stopPreview();
  }
}

watch(() => props.modelValue, stopPreview);
watch(() => props.disabled, (disabled) => {
  if (disabled) stopPreview();
});
onBeforeUnmount(stopPreview);
</script>

<template>
  <div>
    <div class="mb-2 flex items-center justify-between gap-2">
      <label class="text-xs font-semibold text-slate-700 dark:text-stone-200">{{ label }}</label>
      <span v-if="showCount" class="text-[11px] tabular-nums text-slate-400">{{ voices.length }} 个</span>
    </div>
    <div class="grid grid-cols-[minmax(0,1fr)_40px] gap-2">
      <ComposerSelect
        v-if="voices.length"
        :model-value="modelValue"
        :label="label"
        :aria-label="ariaLabel"
        variant="wide"
        :options="voiceOptions"
        :searchable="voices.length > 8"
        :search-placeholder="`搜索${label}`"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <button
        v-else
        type="button"
        class="h-10 min-w-0 rounded-xl border border-black/[0.06] bg-slate-50 px-3 text-left text-xs font-semibold text-slate-400 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-500"
        disabled
      >
        {{ loading ? '正在加载音色' : '暂无可用音色' }}
      </button>
      <button
        type="button"
        class="studio-button grid size-10 place-items-center rounded-xl border border-black/[0.07] bg-slate-50 text-slate-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200"
        :disabled="disabled || !selectedVoice?.demo_audio || loading"
        :aria-label="previewing ? `停止试听${label}` : `试听${label}`"
        :title="previewing ? '停止试听' : '试听音色'"
        @click="togglePreview"
      >
        <Square v-if="previewing" class="size-3.5 fill-current" />
        <Play v-else class="size-4 fill-current" />
      </button>
    </div>
    <p v-if="selectedVoice?.description" class="mt-2 text-xs leading-5 text-slate-500 dark:text-stone-400">
      {{ selectedVoice.description }}
    </p>
    <button
      v-else-if="error"
      type="button"
      class="mt-2 text-left text-xs font-medium text-rose-600 hover:text-rose-700 dark:text-rose-300"
      @click="emit('retry')"
    >
      {{ error }}，点击重试
    </button>
  </div>
</template>
