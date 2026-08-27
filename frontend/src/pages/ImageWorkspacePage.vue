<script setup lang="ts">
import { ArrowDown } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BaseModal from "@/components/BaseModal.vue";
import AgentMemoryPanel from "@/components/image/AgentMemoryPanel.vue";
import HistoryPanel from "@/components/image/HistoryPanel.vue";
import ImageComposer from "@/components/image/ImageComposer.vue";
import ImageLightbox from "@/components/image/ImageLightbox.vue";
import ImageResults from "@/components/image/ImageResults.vue";
import { useImageWorkspace } from "@/composables/useImageWorkspace";
import { resolveApiAssetUrl } from "@/lib/api";
import { sessionState } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const workspace = useImageWorkspace(sessionState.session?.role === "admin");
const {
  imagePrompt,
  imageCount,
  imageRatio,
  imageTier,
  imageWidth,
  imageHeight,
  imageQuality,
  imageModel,
  imageModels,
  referenceImages,
  agentVideos,
  batchProductImage,
  batchFolderImages,
  agentFolder,
  preserveSubject,
  promptEngineMode,
  longTermMemoryEnabled,
  submitPhase,
  conversations,
  selectedConversationId,
  isSubmitting,
  isUploadingAgentVideo,
  isLoadingHistory,
  availableQuota,
  historyOpen,
  deleteConfirm,
  timeoutRetry,
  lightboxOpen,
  lightboxIndex,
  lightboxImages,
  isOpenAIRelayEnabled,
  selectedConversation,
  canResumeAgentWithReferences,
  activeTaskCount,
  deleteConfirmTitle,
  deleteConfirmDescription,
} = workspace;

const resultsViewport = ref<HTMLDivElement | null>(null);
const showScrollLatest = ref(false);
const memoryOpen = ref(false);
const memoryCount = ref(0);
const hasActiveConversation = computed(() => Boolean(selectedConversation.value));
const currentUserName = computed(() => sessionState.session?.name || sessionState.session?.username || "用户");
const currentUserInitial = computed(() => currentUserName.value.trim().slice(0, 1).toUpperCase() || "U");
const currentUserAvatarUrl = computed(() => resolveApiAssetUrl(sessionState.session?.avatarUrl));
const standardIdleTitlePool = [
  "今天想生成什么图片？",
  "描述画面，我来生成图片。",
  "上传参考图，开始创作。",
  "想把图片改成什么样？",
  "这次要生成哪种风格？",
  "给我一个画面想法。",
  "想做一张什么图？",
  "输入提示词，开始生成。",
  "想换背景、改风格，还是重新生成？",
  "从一句描述开始出图。",
  "需要几张不同版本？",
  "参考图准备好了吗？",
] as const;
const agentIdleTitlePool = [
  "今天想让智能体帮你做什么？",
  "想先分析，还是直接出图？",
  "上传参考图，我来拆解视觉方向。",
  "想做主图、详情页，还是优化上一张？",
  "描述目标，我来规划生成方案。",
  "想学习哪张图的风格？",
  "这次要提升哪种商品表现？",
  "把商品图做得更好，从一个需求开始。",
  "给我商品和目标，我来出图。",
  "需要我帮你拆解参考图吗？",
] as const;
function pickIdleTitle(items: readonly string[]) {
  return items[Math.floor(Math.random() * items.length)] ?? items[0] ?? "";
}
const standardIdleTitle = ref(pickIdleTitle(standardIdleTitlePool));
const agentIdleTitle = ref(pickIdleTitle(agentIdleTitlePool));
const idleTitle = computed(() => promptEngineMode.value === "professional" ? agentIdleTitle.value : standardIdleTitle.value);
const idleTitleChars = computed(() => idleTitle.value.split(""));
const idleTitleVisibleChars = ref<boolean[]>([]);
const prefersReducedMotion = ref(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
let idleTitleTimers: number[] = [];

function clearIdleTitleAnimation() {
  for (const timer of idleTitleTimers) window.clearTimeout(timer);
  idleTitleTimers = [];
}

function playIdleTitleAnimation() {
  clearIdleTitleAnimation();
  const chars = idleTitleChars.value;
  idleTitleVisibleChars.value = chars.map(() => false);
  if (prefersReducedMotion.value) {
    idleTitleVisibleChars.value = chars.map(() => true);
    return;
  }
  chars.forEach((_, index) => {
    const timer = window.setTimeout(() => {
      const next = [...idleTitleVisibleChars.value];
      next[index] = true;
      idleTitleVisibleChars.value = next;
    }, index * 60);
    idleTitleTimers.push(timer);
  });
}

async function closeHistory() {
  historyOpen.value = false;
  if (route.query.history === "1") {
    const nextQuery = { ...route.query };
    delete nextQuery.history;
    await router.replace({ path: route.path, query: nextQuery });
  }
}

function onResultsScroll() {
  const element = resultsViewport.value;
  if (!element) return;
  showScrollLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight > 160;
}
function scrollLatest(behavior: ScrollBehavior = "smooth") {
  const element = resultsViewport.value;
  if (!element) return;
  element.scrollTo({ top: element.scrollHeight, behavior });
  showScrollLatest.value = false;
}
watch([() => selectedConversation.value?.updatedAt, () => selectedConversation.value?.turns.length], async () => {
  if (showScrollLatest.value) return;
  await nextTick();
  scrollLatest("smooth");
});

watch(() => route.query.history, (value) => {
  if (value === "1") historyOpen.value = true;
}, { immediate: true });

watch([idleTitle, hasActiveConversation], ([, active]) => {
  if (active) {
    clearIdleTitleAnimation();
    idleTitleVisibleChars.value = [];
    return;
  }
  playIdleTitleAnimation();
}, { immediate: true });

onBeforeUnmount(clearIdleTitleAnimation);
</script>

<template>
  <section class="image-single-page bg-[#F8FAFC] px-3 py-4 dark:bg-[#0f1115] sm:px-5">
    <div class="image-chat-page mx-auto w-full max-w-[1120px]" :class="hasActiveConversation ? 'is-active' : 'is-idle'">
      <div class="image-chat-content min-h-0">
        <Transition name="image-chat-content">
          <div v-if="!hasActiveConversation" key="idle-title" class="image-empty-intro" aria-live="polite">
            <h1 :aria-label="idleTitle">
              <span
                v-for="(char, index) in idleTitleChars"
                :key="`${idleTitle}-${index}`"
                class="image-empty-title-char"
                :class="{ 'is-visible': idleTitleVisibleChars[index] }"
                aria-hidden="true"
              >
                {{ char === ' ' ? '\u00A0' : char }}
              </span>
            </h1>
          </div>
          <div v-else key="results" class="image-chat-results relative min-h-0">
            <div ref="resultsViewport" class="hide-scrollbar h-full min-h-0 overscroll-contain overflow-y-auto px-2 py-4 sm:px-4" @scroll="onResultsScroll">
              <ImageResults
                :conversation="selectedConversation"
                :timeout-retry="timeoutRetry"
                :allow-timeout-retry-continue="!isOpenAIRelayEnabled"
                :user-name="currentUserName"
                :user-initial="currentUserInitial"
                :user-avatar-url="currentUserAvatarUrl"
                :format-conversation-time="workspace.formatConversationTime"
                @open-lightbox="workspace.openLightbox"
                @continue-edit="workspace.continueEdit"
                @delete-prompt="workspace.requestDeletePrompt"
                @delete-results="workspace.requestDeleteResults"
                @reuse-turn-config="workspace.reuseTurnConfig"
                @regenerate-turn="workspace.regenerateTurn"
                @retry-image="workspace.retryImage"
                @retry-batch-item="workspace.retryBatchItem"
                @cancel-turn="workspace.cancelTurn"
                @resume-agent="workspace.resumeAgentTurn"
                @timeout-retry-continue="workspace.continueTimeoutRetry"
                @timeout-retry-cancel="workspace.cancelTimeoutRetry"
                @dismiss-errors="workspace.dismissErrors"
                @open-memory="memoryOpen = true"
                @memory-changed="memoryCount = Math.max(0, memoryCount + $event)"
              />
            </div>
            <button v-if="showScrollLatest" type="button" class="studio-button absolute bottom-4 left-1/2 z-20 inline-flex size-11 -translate-x-1/2 items-center justify-center rounded-xl border border-black/[0.06] bg-white text-slate-700 shadow-[0_18px_44px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-stone-800/95 dark:text-stone-100" aria-label="滚动到最新消息" @click="scrollLatest()">
              <ArrowDown class="size-5" />
            </button>
          </div>
        </Transition>
      </div>

      <div class="image-chat-composer">
        <ImageComposer
          v-model:prompt="imagePrompt"
          v-model:image-count="imageCount"
          v-model:image-ratio="imageRatio"
          v-model:image-tier="imageTier"
          v-model:image-width="imageWidth"
          v-model:image-height="imageHeight"
          v-model:image-quality="imageQuality"
          v-model:image-model="imageModel"
          v-model:preserve-subject="preserveSubject"
          v-model:prompt-engine-mode="promptEngineMode"
          variant="home"
          :image-models="imageModels"
          :available-quota="availableQuota"
          :active-task-count="activeTaskCount"
          :reference-images="referenceImages"
          :agent-videos="agentVideos"
          :batch-product-image="batchProductImage"
          :batch-folder-images="batchFolderImages"
          :agent-folder="agentFolder"
          :allow-reference-only-submit="canResumeAgentWithReferences"
          :long-term-memory-enabled="longTermMemoryEnabled"
          :memory-count="memoryCount"
          :is-submitting="isSubmitting"
          :is-uploading-agent-video="isUploadingAgentVideo"
          :submit-phase="submitPhase"
          @submit="workspace.submit"
          @create-draft="workspace.createDraft"
          @reference-files="workspace.appendReferenceFiles"
          @video-files="workspace.appendAgentVideoFiles"
          @remove-reference="workspace.removeReference"
          @remove-video="workspace.removeAgentVideo"
          @pick-batch-product="workspace.pickBatchProduct"
          @pick-batch-folder="workspace.pickBatchFolder"
          @clear-batch="workspace.clearBatch"
          @open-lightbox="workspace.openLightbox"
          @open-memory="memoryOpen = true"
        />
      </div>
    </div>
  </section>

  <BaseModal :open="historyOpen" title="生成历史记录" width-class="max-w-[520px]" @close="closeHistory">
    <div class="h-[min(72dvh,650px)] p-5">
      <HistoryPanel
        :conversations="conversations"
        :loading="isLoadingHistory"
        :selected-id="selectedConversationId"
        :format-time="workspace.formatConversationTime"
        compact
        @create="workspace.createDraft(); closeHistory()"
        @clear="workspace.requestClearHistory"
        @select="workspace.selectConversation($event); closeHistory()"
        @remove="workspace.requestDeleteConversation"
        @delete-many="workspace.requestDeleteConversations"
        @rename="workspace.renameConversation"
      />
    </div>
  </BaseModal>

  <BaseModal :open="Boolean(deleteConfirm)" :title="deleteConfirmTitle" :description="deleteConfirmDescription" width-class="max-w-[460px]" :show-close="false" @close="deleteConfirm = null">
    <div class="flex justify-end gap-2 p-5">
      <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" @click="deleteConfirm = null">取消</button>
      <button type="button" class="studio-button rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700" @click="workspace.confirmDelete">确认删除</button>
    </div>
  </BaseModal>

  <ImageLightbox :images="lightboxImages" :open="lightboxOpen" :current-index="lightboxIndex" @close="lightboxOpen = false" @change="lightboxIndex = $event" />
  <AgentMemoryPanel
    :open="memoryOpen"
    v-model:enabled="longTermMemoryEnabled"
    :conversation-id="selectedConversationId"
    @close="memoryOpen = false"
    @count-change="memoryCount = $event"
  />
</template>

<style scoped>
.image-single-page {
  height: calc(100dvh - var(--studio-nav-height));
  min-height: 0;
  overflow: hidden;
}

.image-chat-page {
  display: grid;
  height: 100%;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr) auto minmax(0, 1fr);
  row-gap: 22px;
  transition:
    grid-template-rows 420ms cubic-bezier(0.16, 1, 0.3, 1),
    row-gap 320ms ease;
}

.image-chat-page.is-active {
  grid-template-rows: minmax(0, 1fr) auto 0px;
  row-gap: 14px;
}

.image-chat-content {
  align-self: end;
}

.image-chat-page.is-active .image-chat-content {
  align-self: stretch;
}

.image-empty-intro {
  display: grid;
  place-items: center;
  width: min(640px, 100%);
  min-height: 48px;
  margin: 0 auto;
  padding: 0 16px 4px;
  text-align: center;
}

.image-empty-intro h1 {
  margin: 0;
  color: rgb(15 23 42);
  font-size: 1.875rem;
  font-weight: 650;
  line-height: 1.25;
  letter-spacing: 0;
  text-wrap: balance;
}

.dark .image-empty-intro h1 {
  color: rgb(248 250 252);
}

.image-empty-title-char {
  display: inline-block;
  opacity: 0;
  transform: translateY(8px);
  transition:
    opacity 0.4s ease,
    transform 0.4s ease;
  will-change: opacity, transform;
}

.image-empty-title-char.is-visible {
  opacity: 1;
  transform: translateY(0);
}

.image-chat-results {
  position: relative;
  height: 100%;
  min-height: 0;
  border-radius: 12px;
  background: rgb(248 250 252);
}

.dark .image-chat-results {
  background: rgb(17 19 23);
}

.image-chat-composer {
  position: relative;
  z-index: 10;
  width: 100%;
  justify-self: center;
  transition:
    transform 420ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 240ms ease;
}

.image-chat-page.is-idle .image-chat-composer {
  transform: translateY(0);
}

.image-chat-page.is-active .image-chat-composer {
  transform: translateY(0);
}

.image-chat-content-enter-active,
.image-chat-content-leave-active {
  transition:
    opacity 220ms ease,
    transform 320ms cubic-bezier(0.16, 1, 0.3, 1);
}

.image-chat-content-enter-from,
.image-chat-content-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

@media (max-width: 640px) {
  .image-single-page {
    padding-top: 0.75rem;
    padding-bottom: 0.75rem;
  }

  .image-chat-page {
    grid-template-rows: minmax(0, 1fr) auto minmax(0, 0.42fr);
    row-gap: 12px;
  }

  .image-chat-page.is-active {
    grid-template-rows: minmax(0, 1fr) auto 0px;
  }

  .image-empty-intro {
    min-height: 40px;
    padding-inline: 8px;
  }

  .image-empty-intro h1 {
    font-size: 1.375rem;
    line-height: 1.35;
  }

  .image-empty-title-char {
    display: inline-block;
  }
}

@media (prefers-reduced-motion: reduce) {
  .image-chat-page,
  .image-chat-composer,
  .image-chat-content-enter-active,
  .image-chat-content-leave-active,
  .image-empty-title-char {
    transition: none;
  }

  .image-empty-title-char {
    opacity: 1;
    transform: none;
  }
}
</style>
