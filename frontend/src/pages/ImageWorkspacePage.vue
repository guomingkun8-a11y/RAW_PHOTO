<script setup lang="ts">
import { ArrowDown, History, Trash2 } from "@lucide/vue";
import { computed, nextTick, ref, watch } from "vue";

import BaseModal from "@/components/BaseModal.vue";
import HistoryPanel from "@/components/image/HistoryPanel.vue";
import ImageComposer from "@/components/image/ImageComposer.vue";
import ImageLightbox from "@/components/image/ImageLightbox.vue";
import ImageResults from "@/components/image/ImageResults.vue";
import { useImageWorkspace } from "@/composables/useImageWorkspace";
import { resolveApiAssetUrl } from "@/lib/api";
import { getImageConversationStats } from "@/stores/image-conversations";
import { sessionState } from "@/stores/session";

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
  promptTemplates,
  selectedTemplateId,
  referenceImages,
  batchProductImage,
  batchFolderImages,
  preserveSubject,
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
  activeTaskCount,
  deleteConfirmTitle,
  deleteConfirmDescription,
} = workspace;

const resultsViewport = ref<HTMLDivElement | null>(null);
const showScrollLatest = ref(false);
const recent = computed(() => conversations.value.slice(0, 4));
const recentStats = computed(() => new Map(recent.value.map((conversation) => {
  const stats = getImageConversationStats(conversation);
  const success = conversation.turns.reduce((n, turn) => n + turn.images.filter((image) => image.status === "success").length, 0);
  return [conversation.id, { ...stats, success }];
})));
const currentUserName = computed(() => sessionState.session?.name || sessionState.session?.username || "用户");
const currentUserInitial = computed(() => currentUserName.value.trim().slice(0, 1).toUpperCase() || "U");
const currentUserAvatarUrl = computed(() => resolveApiAssetUrl(sessionState.session?.avatarUrl));

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
</script>

<template>
  <section class="image-single-page min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-3 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex min-h-[calc(100dvh_-_var(--studio-nav-height)_-_24px)] w-full max-w-[1120px] flex-col">
      <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[24px] border border-black/[0.06] bg-white shadow-sm dark:border-white/10 dark:bg-[#171a21]">
        <div class="border-b border-black/[0.06] bg-white px-3 py-3 dark:border-white/10 dark:bg-[#171a21] sm:px-4">
          <div class="flex items-center justify-between gap-2">
            <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.06] bg-[#F8FAFC] px-3 text-sm font-medium text-slate-700 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200" @click="historyOpen = true">
              <History class="size-4" />
              历史记录
              <span class="text-xs text-slate-400">({{ conversations.length }})</span>
            </button>
            <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-xl border border-black/[0.06] bg-[#F8FAFC] text-slate-500 disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:bg-white/[0.06]" :disabled="!conversations.length" aria-label="清空历史记录" @click="workspace.requestClearHistory">
              <Trash2 class="size-4" />
            </button>
          </div>

          <div v-if="recent.length" class="hide-scrollbar mt-3 flex gap-2 overflow-x-auto">
            <article
              v-for="conversation in recent"
              :key="conversation.id"
              class="group relative min-w-[198px] rounded-xl border px-3 py-2 pr-9 transition-colors"
              :class="conversation.id === selectedConversationId ? 'border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]' : 'border-black/[0.06] bg-[#F8FAFC] text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200'"
            >
              <button type="button" class="block w-full text-left" @click="workspace.selectConversation(conversation.id)">
                <span class="block truncate text-[13px] font-semibold">{{ conversation.title }}</span>
                <span class="mt-1 block text-[11px] text-slate-500">
                  {{ conversation.turns.length }} 轮 / {{ workspace.formatConversationTime(conversation.updatedAt) }}
                </span>
                <span class="mt-1 inline-flex rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-500 dark:bg-white/[0.08]">
                  {{ recentStats.get(conversation.id)?.running ? `${recentStats.get(conversation.id)?.running} 个生成中` : `${recentStats.get(conversation.id)?.success || 0} 张成功` }}
                </span>
              </button>
              <button type="button" class="studio-button absolute right-1.5 top-1.5 inline-flex size-7 items-center justify-center rounded-lg text-slate-400 opacity-100 hover:bg-rose-50 hover:text-rose-600 sm:opacity-0 sm:group-hover:opacity-100" aria-label="删除图片任务" @click.stop="workspace.requestDeleteConversation(conversation.id)">
                <Trash2 class="size-3.5" />
              </button>
            </article>
          </div>
        </div>

        <div class="relative min-h-0 flex-1">
          <div ref="resultsViewport" class="hide-scrollbar h-full max-h-[min(760px,calc(100dvh_-_360px))] min-h-[420px] overscroll-contain overflow-y-auto bg-[#F8FAFC] px-3 py-4 dark:bg-[#111317] sm:px-5" @scroll="onResultsScroll">
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
              @cancel-turn="workspace.cancelTurn"
              @timeout-retry-continue="workspace.continueTimeoutRetry"
              @timeout-retry-cancel="workspace.cancelTimeoutRetry"
              @dismiss-errors="workspace.dismissErrors"
            />
          </div>
          <button v-if="showScrollLatest" type="button" class="studio-button absolute bottom-4 left-1/2 z-20 inline-flex size-11 -translate-x-1/2 items-center justify-center rounded-xl border border-black/[0.06] bg-white text-slate-700 shadow-[0_18px_44px_rgba(15,23,42,0.12)] dark:border-white/10 dark:bg-stone-800/95 dark:text-stone-100" aria-label="滚动到最新消息" @click="scrollLatest()">
            <ArrowDown class="size-5" />
          </button>
        </div>

        <div class="border-t border-black/[0.06] bg-white p-2 dark:border-white/10 dark:bg-[#171a21] sm:p-3">
          <ImageComposer
            v-model:prompt="imagePrompt"
            v-model:image-count="imageCount"
            v-model:image-ratio="imageRatio"
            v-model:image-tier="imageTier"
            v-model:image-width="imageWidth"
            v-model:image-height="imageHeight"
            v-model:image-quality="imageQuality"
            v-model:image-model="imageModel"
            v-model:selected-template-id="selectedTemplateId"
            v-model:preserve-subject="preserveSubject"
            :image-models="imageModels"
            :prompt-templates="promptTemplates"
            :available-quota="availableQuota"
            :active-task-count="activeTaskCount"
            :reference-images="referenceImages"
            :batch-product-image="batchProductImage"
            :batch-folder-images="batchFolderImages"
            :is-submitting="isSubmitting"
            @submit="workspace.submit"
            @create-draft="workspace.createDraft"
            @reference-files="workspace.appendReferenceFiles"
            @remove-reference="workspace.removeReference"
            @pick-batch-product="workspace.pickBatchProduct"
            @pick-batch-folder="workspace.pickBatchFolder"
            @clear-batch="workspace.clearBatch"
          />
        </div>
      </div>
    </div>
  </section>

  <BaseModal :open="historyOpen" title="历史记录" width-class="max-w-[460px]" @close="historyOpen = false">
    <div class="h-[min(72dvh,650px)] p-5">
      <HistoryPanel
        :conversations="conversations"
        :loading="isLoadingHistory"
        :selected-id="selectedConversationId"
        :format-time="workspace.formatConversationTime"
        compact
        @create="workspace.createDraft(); historyOpen = false"
        @clear="workspace.requestClearHistory"
        @select="workspace.selectConversation($event); historyOpen = false"
        @remove="workspace.requestDeleteConversation"
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
</template>
