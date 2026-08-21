<script setup lang="ts">
import { ArrowDown } from "@lucide/vue";
import { computed, nextTick, ref, watch } from "vue";
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
</script>

<template>
  <section class="image-single-page bg-[#F8FAFC] px-3 py-4 dark:bg-[#0f1115] sm:px-5">
    <div class="image-chat-page mx-auto w-full max-w-[1120px]" :class="hasActiveConversation ? 'is-active' : 'is-idle'">
      <div class="image-chat-content min-h-0">
        <Transition name="image-chat-content">
          <div v-if="hasActiveConversation" class="image-chat-results relative min-h-0">
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
          :batch-product-image="batchProductImage"
          :batch-folder-images="batchFolderImages"
          :agent-folder="agentFolder"
          :allow-reference-only-submit="canResumeAgentWithReferences"
          :long-term-memory-enabled="longTermMemoryEnabled"
          :memory-count="memoryCount"
          :is-submitting="isSubmitting"
          :submit-phase="submitPhase"
          @submit="workspace.submit"
          @create-draft="workspace.createDraft"
          @reference-files="workspace.appendReferenceFiles"
          @remove-reference="workspace.removeReference"
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
}

@media (prefers-reduced-motion: reduce) {
  .image-chat-page,
  .image-chat-composer,
  .image-chat-content-enter-active,
  .image-chat-content-leave-active {
    transition: none;
  }
}
</style>
