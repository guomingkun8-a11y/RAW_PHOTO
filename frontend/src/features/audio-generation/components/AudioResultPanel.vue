<script setup lang="ts">
import { CheckCircle2, Copy, Download } from "@lucide/vue";
import { computed } from "vue";
import { toast } from "vue-sonner";

import {
  DOUBAO_TTS_MODEL,
  type AudioGenerationTask,
  type AudioVoice,
  type DoubaoAudioGenerationParams,
  type GemAudioGenerationParams,
} from "@/features/audio-generation/types/audio-generation";
import { resolveApiAssetUrl } from "@/lib/api";

const props = defineProps<{
  result: AudioGenerationTask;
  voice?: AudioVoice;
}>();

const costLabel = computed(() => {
  const cost = props.result.cost;
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "费用待上游返回";
  return `￥${cost.toLocaleString("zh-CN", { maximumFractionDigits: 6 })}`;
});

const doubaoParams = computed<DoubaoAudioGenerationParams | null>(() => (
  props.result.model === DOUBAO_TTS_MODEL && "speech_rate" in props.result.params
    ? props.result.params
    : null
));
const gemParams = computed<GemAudioGenerationParams | null>(() => (
  props.result.model !== DOUBAO_TTS_MODEL ? props.result.params : null
));
const modelLabel = computed(() => props.result.model === DOUBAO_TTS_MODEL ? "豆包 2.0" : "GEM 3.1");
const formatLabel = computed(() => {
  const value = doubaoParams.value?.format;
  if (!value) return "";
  return value === "ogg_opus" ? "OGG OPUS" : value.toUpperCase();
});
const voiceLabel = computed(() => {
  const speakers = gemParams.value?.speaker_voice_configs;
  if (speakers?.length) return speakers.map((item) => item.speaker).join(" / ");
  return props.voice?.name || props.result.params.voice_id || "默认音色";
});
const canPreview = computed(() => doubaoParams.value?.format !== "pcm");
const resultUrl = computed(() => resolveApiAssetUrl(props.result.audio_url || ""));

async function copyUrl() {
  try {
    await navigator.clipboard.writeText(resultUrl.value);
    toast.success("音频链接已复制");
  } catch {
    toast.error("复制失败");
  }
}
</script>

<template>
  <section class="studio-card overflow-hidden bg-white dark:bg-[#171a21]" aria-label="生成结果">
    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-black/[0.06] px-4 py-3.5 dark:border-white/10 sm:px-5">
      <div class="flex items-center gap-2 text-sm font-semibold text-slate-950 dark:text-stone-50">
        <CheckCircle2 class="size-4 text-emerald-500" />
        音频已生成
      </div>
      <div class="flex items-center gap-1.5">
        <button type="button" class="studio-button grid size-9 place-items-center rounded-xl border border-black/[0.07] text-slate-500 dark:border-white/10 dark:text-stone-300" aria-label="复制音频链接" title="复制音频链接" @click="copyUrl">
          <Copy class="size-4" />
        </button>
        <a :href="resultUrl" target="_blank" rel="noopener" download class="studio-button grid size-9 place-items-center rounded-xl bg-slate-950 text-white dark:bg-white dark:text-slate-950" aria-label="下载音频" title="下载音频">
          <Download class="size-4" />
        </a>
      </div>
    </div>

    <div class="p-4 sm:p-5">
      <audio v-if="canPreview" class="h-12 w-full" :src="resultUrl" controls preload="metadata" />
      <div v-else class="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:bg-white/[0.04] dark:text-stone-300">
        PCM 文件请下载后使用音频工具打开。
      </div>

      <div class="mt-4 flex flex-wrap gap-2 text-xs text-slate-600 dark:text-stone-300">
        <span class="rounded-lg bg-slate-100 px-2.5 py-1.5 dark:bg-white/[0.06]">{{ modelLabel }}</span>
        <span class="rounded-lg bg-slate-100 px-2.5 py-1.5 dark:bg-white/[0.06]">{{ voiceLabel }}</span>
        <span v-if="doubaoParams" class="rounded-lg bg-slate-100 px-2.5 py-1.5 tabular-nums dark:bg-white/[0.06]">语速 {{ doubaoParams.speech_rate }}</span>
        <span v-if="formatLabel" class="rounded-lg bg-slate-100 px-2.5 py-1.5 dark:bg-white/[0.06]">{{ formatLabel }}</span>
        <span v-if="gemParams?.speaker_voice_configs?.length" class="rounded-lg bg-slate-100 px-2.5 py-1.5 dark:bg-white/[0.06]">双人对话</span>
        <span class="rounded-lg bg-emerald-50 px-2.5 py-1.5 font-semibold text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300">{{ costLabel }}</span>
      </div>
    </div>
  </section>
</template>
