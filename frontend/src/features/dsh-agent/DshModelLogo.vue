<script setup lang="ts">
import { Bot } from "@lucide/vue";
import { computed } from "vue";
import gemini from "./assets/model-logos/gemini-color.svg";
import openai from "./assets/model-logos/openai.svg";
import deepseek from "./assets/model-logos/deepseek-color.svg";

const props = defineProps<{ model?: string }>();
const logo = computed(() => {
  if (props.model?.startsWith("gem-") || props.model === "banana-2") return gemini;
  if (props.model?.startsWith("tt-") || props.model?.startsWith("gpt-")) return openai;
  if (props.model?.startsWith("deepseek-")) return deepseek;
  return "";
});
</script>

<template>
  <img v-if="logo" class="dsh-model-logo" :class="{ 'is-monochrome': logo === openai }" :src="logo" alt="" width="18" height="18" draggable="false" />
  <Bot v-else class="dsh-model-logo" aria-hidden="true" />
</template>

<style scoped>
.dsh-model-logo { display: block; width: 18px; height: 18px; flex: none; object-fit: contain; }
.dark .dsh-model-logo.is-monochrome { filter: invert(1); }
</style>
