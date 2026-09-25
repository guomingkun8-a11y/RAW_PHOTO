<script setup lang="ts">
import { AudioLines, LoaderCircle, Sparkles, Square } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { toast } from "vue-sonner";

import AudioResultPanel from "@/features/audio-generation/components/AudioResultPanel.vue";
import AudioSettingsPanel from "@/features/audio-generation/components/AudioSettingsPanel.vue";
import { useAudioTaskPolling } from "@/features/audio-generation/composables/useAudioTaskPolling";
import {
  cancelAudioGenerationTask,
  createAudioGenerationTask,
  fetchAudioVoices,
} from "@/features/audio-generation/services/audio-generation-api";
import {
  AUDIO_GENERATION_MODEL,
  AUDIO_PROMPT_MAX_CHARS,
  DEFAULT_AUDIO_VOICE_ID,
  DOUBAO_TTS_MODEL,
  GEM_TTS_MODEL,
  type AudioEmotion,
  type AudioGenerationModel,
  type AudioGenerationRequest,
  type AudioGenerationTask,
  type AudioOutputFormat,
  type AudioSpeechRate,
  type AudioVoice,
  type AudioVoiceListResponse,
  type GemReadingMode,
} from "@/features/audio-generation/types/audio-generation";
import { storageKey } from "@/lib/storage-namespace";

const MODEL_PRESENTATION: Record<AudioGenerationModel, { eyebrow: string; placeholder: string }> = {
  [DOUBAO_TTS_MODEL]: {
    eyebrow: "豆包语音合成 2.0",
    placeholder: "输入需要合成的文本",
  },
  [GEM_TTS_MODEL]: {
    eyebrow: "GEM 原生语音合成 3.1",
    placeholder: "输入朗读内容，可直接描述语气与节奏",
  },
};

const prompt = ref("");
const model = ref<AudioGenerationModel>(AUDIO_GENERATION_MODEL);
const voiceCatalogs = ref<Partial<Record<AudioGenerationModel, AudioVoiceListResponse>>>({});
const voiceIds = ref<Record<AudioGenerationModel, string>>({
  [DOUBAO_TTS_MODEL]: DEFAULT_AUDIO_VOICE_ID,
  [GEM_TTS_MODEL]: "",
});
const secondaryVoiceId = ref("");
const speechRate = ref<AudioSpeechRate>(0);
const emotion = ref<AudioEmotion>("auto");
const emotionScale = ref(4);
const format = ref<AudioOutputFormat>("mp3");
const gemReadingMode = ref<GemReadingMode>("single");
const primarySpeaker = ref("旁白");
const secondarySpeaker = ref("嘉宾");
const loadingModel = ref<AudioGenerationModel | null>(null);
const voiceErrors = ref<Partial<Record<AudioGenerationModel, string>>>({});
const generating = ref(false);
const elapsedSeconds = ref(0);
const currentTask = ref<AudioGenerationTask | null>(null);
const result = ref<AudioGenerationTask | null>(null);
const ACTIVE_TASK_STORAGE_KEY = storageKey("audio-generation-active-task");
let elapsedTimer = 0;
let voiceRequestVersion = 0;

const normalizedPrompt = computed(() => prompt.value.trim());
const voices = computed<AudioVoice[]>(() => voiceCatalogs.value[model.value]?.voices || []);
const voiceId = computed(() => voiceIds.value[model.value]);
const voicesLoading = computed(() => loadingModel.value === model.value);
const voicesError = computed(() => voiceErrors.value[model.value] || "");
const selectedVoice = computed(() => voices.value.find((voice) => voice.voice_id === voiceId.value));
const modelPresentation = computed(() => MODEL_PRESENTATION[model.value]);
const duplicateSpeakerNames = computed(() => (
  primarySpeaker.value.trim().toLocaleLowerCase() === secondarySpeaker.value.trim().toLocaleLowerCase()
));
const canGenerate = computed(() => {
  if (!normalizedPrompt.value || !voiceId.value || generating.value || voicesLoading.value) return false;
  if (model.value !== GEM_TTS_MODEL || gemReadingMode.value === "single") return true;
  return Boolean(
    secondaryVoiceId.value
    && primarySpeaker.value.trim()
    && secondarySpeaker.value.trim()
    && !duplicateSpeakerNames.value,
  );
});
const generationStatus = computed(() => {
  if (!generating.value) return "准备就绪";
  const state = currentTask.value?.status === "queued" ? "正在排队" : "正在生成";
  return `${state} · ${elapsedSeconds.value}s`;
});

function clearPersistedTask() {
  try { window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY); } catch { /* Storage can be unavailable. */ }
}

function persistTask(taskId: string) {
  try { window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, taskId); } catch { /* Storage can be unavailable. */ }
}

const taskPolling = useAudioTaskPolling(
  (task) => {
    currentTask.value = task;
    if (task.status === "queued" || task.status === "running") return;
    generating.value = false;
    stopElapsedTimer();
    clearPersistedTask();
    if (task.status === "success" && task.audio_url) {
      result.value = task;
      toast.success("音频生成完成");
    } else if (task.status === "canceled") {
      toast.info(task.cancellation_pending ? "已请求取消，上游任务可能仍会产生费用" : "音频任务已取消");
    } else {
      toast.error(task.error || "音频生成失败");
    }
  },
  (error) => {
    console.warn("Audio task polling failed", error);
  },
);

function isUsableVoiceCatalog(value: unknown): value is AudioVoiceListResponse {
  return Boolean(value)
    && typeof value === "object"
    && Array.isArray((value as AudioVoiceListResponse).voices)
    && (value as AudioVoiceListResponse).voices.length > 0;
}

function selectDefaultVoices(targetModel: AudioGenerationModel, response: AudioVoiceListResponse) {
  const availableIds = new Set(response.voices.map((voice) => voice.voice_id));
  const currentVoiceId = voiceIds.value[targetModel];
  if (!currentVoiceId || !availableIds.has(currentVoiceId)) {
    voiceIds.value = {
      ...voiceIds.value,
      [targetModel]: response.default_voice_id || response.voices[0]?.voice_id || "",
    };
  }
  if (targetModel === GEM_TTS_MODEL && (!secondaryVoiceId.value || !availableIds.has(secondaryVoiceId.value))) {
    secondaryVoiceId.value = response.voices.find(
      (voice) => voice.voice_id !== voiceIds.value[GEM_TTS_MODEL],
    )?.voice_id || response.voices[0]?.voice_id || "";
  }
}

async function loadVoices(force = false) {
  const targetModel = model.value;
  const cached: unknown = voiceCatalogs.value[targetModel];
  if (isUsableVoiceCatalog(cached) && !force) {
    selectDefaultVoices(targetModel, cached);
    return;
  }
  if (cached && !isUsableVoiceCatalog(cached)) {
    const nextCatalogs = { ...voiceCatalogs.value };
    delete nextCatalogs[targetModel];
    voiceCatalogs.value = nextCatalogs;
  }

  const requestVersion = ++voiceRequestVersion;
  loadingModel.value = targetModel;
  voiceErrors.value = { ...voiceErrors.value, [targetModel]: "" };
  try {
    const response = await fetchAudioVoices(targetModel);
    voiceCatalogs.value = { ...voiceCatalogs.value, [targetModel]: response };
    selectDefaultVoices(targetModel, response);
  } catch (error) {
    const message = error instanceof Error ? error.message : "音色加载失败";
    voiceErrors.value = { ...voiceErrors.value, [targetModel]: message };
    if (!voiceCatalogs.value[targetModel] && targetModel === DOUBAO_TTS_MODEL) {
      const fallback: AudioVoiceListResponse = {
        model: DOUBAO_TTS_MODEL,
        voices: [{ voice_id: DEFAULT_AUDIO_VOICE_ID, name: "Vivi" }],
        total: 1,
        default_voice_id: DEFAULT_AUDIO_VOICE_ID,
      };
      voiceCatalogs.value = { ...voiceCatalogs.value, [targetModel]: fallback };
      selectDefaultVoices(targetModel, fallback);
    }
  } finally {
    if (requestVersion === voiceRequestVersion) loadingModel.value = null;
  }
}

function selectModel(value: AudioGenerationModel) {
  if (value === model.value) return;
  model.value = value;
  result.value = null;
  void loadVoices();
}

function setVoiceId(value: string) {
  voiceIds.value = { ...voiceIds.value, [model.value]: value };
  if (model.value === GEM_TTS_MODEL && secondaryVoiceId.value === value) {
    secondaryVoiceId.value = voices.value.find((voice) => voice.voice_id !== value)?.voice_id || value;
  }
}

function startElapsedTimer() {
  window.clearInterval(elapsedTimer);
  elapsedSeconds.value = 0;
  elapsedTimer = window.setInterval(() => {
    elapsedSeconds.value += 1;
  }, 1000);
}

function stopElapsedTimer() {
  window.clearInterval(elapsedTimer);
  elapsedTimer = 0;
}

function buildRequest(): AudioGenerationRequest {
  if (model.value === DOUBAO_TTS_MODEL) {
    return {
      model: DOUBAO_TTS_MODEL,
      prompt: normalizedPrompt.value,
      params: {
        voice_id: voiceId.value,
        speech_rate: speechRate.value,
        emotion: emotion.value,
        ...(emotion.value === "auto" ? {} : { emotion_scale: emotionScale.value }),
        format: format.value,
      },
    };
  }

  return {
    model: GEM_TTS_MODEL,
    prompt: normalizedPrompt.value,
    params: gemReadingMode.value === "single"
      ? { voice_id: voiceId.value }
      : {
        speaker_voice_configs: [
          { speaker: primarySpeaker.value.trim(), voice_id: voiceId.value },
          { speaker: secondarySpeaker.value.trim(), voice_id: secondaryVoiceId.value },
        ],
      },
  };
}

async function submit() {
  if (!canGenerate.value) return;
  generating.value = true;
  result.value = null;
  startElapsedTimer();
  try {
    const clientTaskId = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `audio-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const task = await createAudioGenerationTask({
      ...buildRequest(),
      client_task_id: clientTaskId,
    });
    currentTask.value = task;
    persistTask(task.id);
    taskPolling.start(task.id);
  } catch (error) {
    generating.value = false;
    stopElapsedTimer();
    toast.error(error instanceof Error ? error.message : "音频生成失败");
  }
}

async function cancelCurrentTask() {
  const task = currentTask.value;
  if (!task || !generating.value) return;
  try {
    currentTask.value = await cancelAudioGenerationTask(task.id);
    taskPolling.start(task.id);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "取消音频任务失败");
  }
}

onMounted(() => {
  void loadVoices();
  try {
    const taskId = window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY) || "";
    if (taskId) {
      generating.value = true;
      startElapsedTimer();
      taskPolling.start(taskId);
    }
  } catch { /* Storage can be unavailable. */ }
});
onBeforeUnmount(stopElapsedTimer);
</script>

<template>
  <section class="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1480px] flex-col gap-5">
      <header class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div class="flex items-center gap-2 text-sm font-medium text-[#315be8] dark:text-[#9db5ff]">
            <AudioLines class="size-4" />
            {{ modelPresentation.eyebrow }}
          </div>
          <h1 class="mt-2 text-[30px] font-semibold text-slate-950 dark:text-stone-50">音频生成</h1>
        </div>
        <span class="rounded-lg bg-white px-3 py-2 text-xs font-medium text-slate-500 dark:bg-white/[0.06] dark:text-stone-300">{{ model }}</span>
      </header>

      <div class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <main class="min-w-0 space-y-4">
          <section class="studio-card audio-generation-panel overflow-hidden bg-white dark:bg-[#171a21]">
            <div class="flex items-center justify-between gap-3 border-b border-black/[0.06] px-4 py-3.5 dark:border-white/10 sm:px-5">
              <label for="audio-prompt" class="text-sm font-semibold text-slate-950 dark:text-stone-50">文本内容</label>
              <span class="text-xs tabular-nums text-slate-400">{{ prompt.length.toLocaleString() }} / {{ AUDIO_PROMPT_MAX_CHARS.toLocaleString() }}</span>
            </div>
            <textarea
              id="audio-prompt"
              v-model="prompt"
              class="block min-h-[340px] w-full resize-y bg-transparent px-4 py-4 text-[15px] leading-7 text-slate-900 outline-none placeholder:text-slate-400 dark:text-stone-100 dark:placeholder:text-stone-500 sm:min-h-[420px] sm:px-5"
              :maxlength="AUDIO_PROMPT_MAX_CHARS"
              :placeholder="modelPresentation.placeholder"
              :disabled="generating"
              data-testid="audio-prompt"
            />
            <div class="flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.06] px-4 py-3.5 dark:border-white/10 sm:px-5">
              <span class="text-xs font-medium text-slate-500 dark:text-stone-400" aria-live="polite">{{ generationStatus }}</span>
              <div class="flex items-center gap-2">
                <button
                  v-if="generating"
                  type="button"
                  class="studio-button inline-flex size-11 items-center justify-center rounded-xl border border-black/[0.08] text-slate-600 dark:border-white/10 dark:text-stone-300"
                  aria-label="取消音频任务"
                  title="取消音频任务"
                  @click="cancelCurrentTask"
                >
                  <Square class="size-4" />
                </button>
                <button
                  type="button"
                  class="studio-button inline-flex h-11 min-w-[132px] items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45 dark:bg-white dark:text-slate-950"
                  :disabled="!canGenerate"
                  data-testid="audio-generate"
                  @click="submit"
                >
                  <LoaderCircle v-if="generating" class="size-4 animate-spin" />
                  <Sparkles v-else class="size-4" />
                  {{ generating ? '生成中' : '生成音频' }}
                </button>
              </div>
            </div>
          </section>

          <AudioResultPanel v-if="result" :result="result" :voice="selectedVoice" />
        </main>

        <AudioSettingsPanel
          :model="model"
          :voices="voices"
          :voice-id="voiceId"
          :secondary-voice-id="secondaryVoiceId"
          :speech-rate="speechRate"
          :emotion="emotion"
          :emotion-scale="emotionScale"
          :format="format"
          :gem-reading-mode="gemReadingMode"
          :primary-speaker="primarySpeaker"
          :secondary-speaker="secondarySpeaker"
          :voices-loading="voicesLoading"
          :voices-error="voicesError"
          :disabled="generating"
          @update:model="selectModel"
          @update:voice-id="setVoiceId"
          @update:secondary-voice-id="secondaryVoiceId = $event"
          @update:speech-rate="speechRate = $event"
          @update:emotion="emotion = $event"
          @update:emotion-scale="emotionScale = $event"
          @update:format="format = $event"
          @update:gem-reading-mode="gemReadingMode = $event"
          @update:primary-speaker="primarySpeaker = $event"
          @update:secondary-speaker="secondarySpeaker = $event"
          @retry-voices="loadVoices(true)"
        />
      </div>
    </div>
  </section>
</template>
