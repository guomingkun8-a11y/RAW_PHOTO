<script setup lang="ts">
import { ArrowDown, AudioLines, Bot, ChevronRight, Film, ImageIcon, LoaderCircle, Trash2 } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import AudioTaskHistoryPanel from "@/features/audio-generation/components/AudioTaskHistoryPanel.vue";
import AgentMemoryPanel from "@/components/image/AgentMemoryPanel.vue";
import HistoryPanel from "@/components/image/HistoryPanel.vue";
import ImageComposer from "@/components/image/ImageComposer.vue";
import ImageLightbox from "@/components/image/ImageLightbox.vue";
import ImageResults from "@/components/image/ImageResults.vue";
import { useImageWorkspace } from "@/composables/useImageWorkspace";
import { useVideoTaskPolling, videoTaskDeletionReason, videoTaskNeedsPolling } from "@/composables/useVideoTaskPolling";
import {
  deleteVideoAgentConversation,
  deleteVideoAgentPlan,
  deleteVideoGenerationConversation,
  deleteVideoGenerationTask,
  fetchVideoAgentPlans,
  fetchVideoGenerationTasks,
  resolveApiAssetUrl,
  type VideoAgentPlan,
  type VideoGenerationTask,
} from "@/lib/api";
import { sessionState } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const workspace = useImageWorkspace(sessionState.session?.role === "admin");
watch(() => route.query.prompt_template, (value) => {
  if (typeof value === "string" && value) workspace.consumePendingPromptTemplate();
}, { immediate: true });
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
  isLoadingMoreHistory,
  hasMoreHistory,
  historyTotal,
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
const historyType = ref<"image" | "video" | "audio">(
  route.query.type === "video" ? "video" : route.query.type === "audio" ? "audio" : "image",
);
const videoHistoryTasks = ref<VideoGenerationTask[]>([]);
const videoHistoryAgentPlans = ref<VideoAgentPlan[]>([]);
const videoHistoryLoading = ref(false);
const videoHistoryCursor = ref<string | null>(null);
const videoHistoryLoadingMore = ref(false);
const videoHistoryError = ref("");
let videoHistoryDisposed = false;
watch(() => route.query.prompt_template, (value) => {
  if (typeof value === "string" && value) workspace.consumePendingPromptTemplate();
}, { immediate: true });
type VideoHistoryConversation = {
  key: string;
  id: string;
  kind: "generation" | "agent";
  title: string;
  modelLabel: string;
  owner: string;
  turnCount: number;
  taskId?: string;
  planId?: string;
  isLegacy: boolean;
  activeCount?: number;
  completedCount?: number;
  generatedCount?: number;
  updatedAt: string;
  totalCost?: number;
  deleteReason?: string;
};
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

function videoHistoryOwner(item: VideoGenerationTask | VideoAgentPlan) {
  return item.owner_name || item.owner_username || item.owner_id || "未知用户";
}
function videoHistoryTimestamp(value?: string) {
  const time = value ? new Date(value).getTime() : 0;
  return Number.isFinite(time) ? time : 0;
}
function videoHistoryDate(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
function videoHistoryCost(value?: number) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "";
  return `花费 $${Number(value).toFixed(4)}`;
}
function videoHistoryConversationKey(task: VideoGenerationTask) {
  return task.conversation_id || `legacy:${task.id}`;
}
function videoAgentGenerationTaskIds(plans: VideoAgentPlan[]) {
  return Array.from(new Set(plans.flatMap((plan) => (plan.attachments || [])
    .filter((attachment) => attachment.kind === "generation")
    .map((attachment) => String(attachment.task_id || "").trim())
    .filter(Boolean))));
}
const videoHistoryConversations = computed<VideoHistoryConversation[]>(() => {
  const agentGenerationTaskIds = new Set(videoAgentGenerationTaskIds(videoHistoryAgentPlans.value));
  const generationTaskById = new Map(videoHistoryTasks.value.map((task) => [task.id, task]));
  const groups = new Map<string, VideoGenerationTask[]>();
  for (const task of videoHistoryTasks.value) {
    if (agentGenerationTaskIds.has(task.id)) continue;
    const key = videoHistoryConversationKey(task);
    groups.set(key, [...(groups.get(key) || []), task]);
  }
  const generationConversations = Array.from(groups.entries())
    .map(([id, items]) => {
      const sorted = [...items].sort(
        (a, b) => videoHistoryTimestamp(b.updated_at || b.created_at) - videoHistoryTimestamp(a.updated_at || a.created_at),
      );
      const latestTask = sorted[0]!;
      const totalCost = sorted.reduce((sum, task) => {
        const cost = Number(task.cost);
        return Number.isFinite(cost) ? sum + cost : sum;
      }, 0);
      return {
        key: `generation:${id}`,
        id,
        kind: "generation" as const,
        title: latestTask?.prompt || "未记录视频提示词",
        modelLabel: latestTask.model || "未指定模型",
        owner: videoHistoryOwner(latestTask),
        turnCount: sorted.length,
        taskId: latestTask.id,
        isLegacy: id.startsWith("legacy:"),
        activeCount: sorted.filter(videoTaskNeedsPolling).length,
        deleteReason: sorted.map(videoTaskDeletionReason).find(Boolean),
        completedCount: sorted.filter((task) => task.status === "success").length,
        updatedAt: latestTask?.updated_at || latestTask?.created_at || "",
        totalCost: totalCost > 0 ? totalCost : undefined,
      };
    });

  const agentGroups = new Map<string, VideoAgentPlan[]>();
  for (const plan of videoHistoryAgentPlans.value) {
    const id = plan.conversation_id || `legacy-agent:${plan.id}`;
    agentGroups.set(id, [...(agentGroups.get(id) || []), plan]);
  }
  const agentConversations = Array.from(agentGroups.entries()).map(([id, items]) => {
    const sorted = [...items].sort(
      (a, b) => videoHistoryTimestamp(b.created_at) - videoHistoryTimestamp(a.created_at),
    );
    const latestPlan = sorted[0]!;
    const firstPlan = sorted[sorted.length - 1] || latestPlan;
    const generatedTaskIds = videoAgentGenerationTaskIds(items);
    const generatedTasks = generatedTaskIds
      .map((taskId) => generationTaskById.get(taskId))
      .filter((task): task is VideoGenerationTask => Boolean(task));
    const totalCost = generatedTasks.reduce((sum, task) => {
      const cost = Number(task.cost);
      return Number.isFinite(cost) ? sum + cost : sum;
    }, 0);
    const updatedAt = [
      latestPlan.created_at,
      ...generatedTasks.map((task) => task.updated_at || task.created_at),
    ].sort((left, right) => videoHistoryTimestamp(right) - videoHistoryTimestamp(left))[0] || latestPlan.created_at;
    return {
      key: `agent:${id}`,
      id,
      kind: "agent" as const,
      title: firstPlan.prompt || "视频智能体对话",
      modelLabel: [
        latestPlan.chat_model ? `视频智能体 · ${latestPlan.chat_model}` : "视频智能体",
        generatedTaskIds.length ? `生成 ${generatedTaskIds.length} 段` : "",
      ].filter(Boolean).join(" · "),
      owner: videoHistoryOwner(latestPlan),
      turnCount: sorted.length,
      planId: latestPlan.id,
      isLegacy: id.startsWith("legacy-agent:"),
      activeCount: generatedTasks.filter(videoTaskNeedsPolling).length,
      deleteReason: generatedTasks.map(videoTaskDeletionReason).find(Boolean),
      completedCount: generatedTasks.filter((task) => task.status === "success").length,
      generatedCount: generatedTaskIds.length,
      updatedAt,
      totalCost: totalCost > 0 ? totalCost : undefined,
    };
  });

  return [...generationConversations, ...agentConversations]
    .sort((a, b) => videoHistoryTimestamp(b.updatedAt) - videoHistoryTimestamp(a.updatedAt));
});
function videoHistoryConversationMeta(conversation: VideoHistoryConversation) {
  return [
    `${conversation.kind === "generation" && videoHistoryCursor.value ? "已加载 " : ""}${conversation.turnCount} 轮`,
    videoHistoryDate(conversation.updatedAt),
    `用户：${conversation.owner}`,
    conversation.activeCount ? `进行中 ${conversation.activeCount}` : "",
    conversation.completedCount ? `完成 ${conversation.completedCount}` : "",
    videoHistoryCost(conversation.totalCost),
  ].filter(Boolean);
}
const videoHistoryDeleteTarget = ref<VideoHistoryConversation | null>(null);
const deletingVideoHistoryKey = ref("");
const videoHistoryDeleteDescription = computed(() => {
  const conversation = videoHistoryDeleteTarget.value;
  if (!conversation) return "";
  const label = conversation.kind === "agent" ? "视频智能体对话" : "视频生成记录";
  const countLabel = conversation.kind === "agent" ? "轮问答" : "个任务";
  const generationLabel = conversation.kind === "agent" && conversation.generatedCount
    ? `及关联的 ${conversation.generatedCount} 个视频生成任务`
    : "";
  const count = conversation.kind === "generation" && videoHistoryCursor.value ? "全部" : String(conversation.turnCount);
  return `确认永久删除这条${label}吗？该会话下的 ${count} ${countLabel}${generationLabel}会被彻底删除，删除后无法恢复。`;
});

function requestDeleteVideoHistory(conversation: VideoHistoryConversation) {
  if (conversation.deleteReason) {
    toast.error(conversation.deleteReason);
    return;
  }
  videoHistoryDeleteTarget.value = conversation;
}

function cancelDeleteVideoHistory() {
  if (!deletingVideoHistoryKey.value) videoHistoryDeleteTarget.value = null;
}

async function confirmDeleteVideoHistory() {
  const conversation = videoHistoryDeleteTarget.value;
  if (!conversation || deletingVideoHistoryKey.value) return;
  const current = videoHistoryConversations.value.find((item) => item.key === conversation.key);
  if (current?.deleteReason) {
    toast.error(current.deleteReason);
    return;
  }
  deletingVideoHistoryKey.value = conversation.key;
  try {
    if (conversation.kind === "agent") {
      const matchingPlans = videoHistoryAgentPlans.value.filter((plan) => (
        conversation.isLegacy ? plan.id === conversation.planId : plan.conversation_id === conversation.id
      ));
      const generationTaskIds = new Set(videoAgentGenerationTaskIds(matchingPlans));
      if (conversation.isLegacy) {
        const planId = String(conversation.planId || "").trim();
        if (!planId) throw new Error("未找到可删除的视频智能体记录");
        await deleteVideoAgentPlan(planId);
        videoHistoryAgentPlans.value = videoHistoryAgentPlans.value.filter((plan) => plan.id !== planId);
      } else {
        await deleteVideoAgentConversation(conversation.id);
        videoHistoryAgentPlans.value = videoHistoryAgentPlans.value.filter((plan) => plan.conversation_id !== conversation.id);
      }
      videoHistoryTasks.value = videoHistoryTasks.value.filter((task) => !generationTaskIds.has(task.id));
    } else if (conversation.isLegacy) {
      const taskId = String(conversation.taskId || "").trim();
      if (!taskId) throw new Error("未找到可删除的视频生成记录");
      await deleteVideoGenerationTask(taskId);
      videoHistoryTasks.value = videoHistoryTasks.value.filter((task) => task.id !== taskId);
    } else {
      await deleteVideoGenerationConversation(conversation.id);
      videoHistoryTasks.value = videoHistoryTasks.value.filter((task) => task.conversation_id !== conversation.id);
    }
    videoHistoryDeleteTarget.value = null;
    toast.success("视频记录已永久删除");
    if (!videoHistoryConversations.value.length && videoHistoryCursor.value) void loadVideoHistory(true);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除视频记录失败");
  } finally {
    deletingVideoHistoryKey.value = "";
  }
}

useVideoTaskPolling(
  () => historyOpen.value && historyType.value === "video" && !videoHistoryLoading.value ? videoHistoryTasks.value : [],
  (updates) => {
    const byId = new Map(updates.map((task) => [task.id, task]));
    videoHistoryTasks.value = videoHistoryTasks.value.map((task) => byId.get(task.id) || task);
  },
);

async function loadVideoHistory(append = false) {
  if (videoHistoryLoading.value || videoHistoryLoadingMore.value) return;
  if (append && !videoHistoryCursor.value) return;
  if (append) videoHistoryLoadingMore.value = true;
  else videoHistoryLoading.value = true;
  videoHistoryError.value = "";
  try {
    const [taskResult, agentResult] = await Promise.all([
      fetchVideoGenerationTasks([], { limit: 50, cursor: append ? videoHistoryCursor.value! : undefined }),
      append ? Promise.resolve({ items: videoHistoryAgentPlans.value }) : fetchVideoAgentPlans({ limit: 500 }),
    ]);
    if (videoHistoryDisposed) return;
    const merged = new Map((append ? videoHistoryTasks.value : []).map((task) => [task.id, task]));
    taskResult.items.forEach((task) => merged.set(task.id, task));
    const missingGenerationIds = videoAgentGenerationTaskIds(agentResult.items).filter((id) => !merged.has(id));
    if (missingGenerationIds.length) {
      const related = await fetchVideoGenerationTasks(missingGenerationIds);
      if (videoHistoryDisposed) return;
      related.items.forEach((task) => merged.set(task.id, task));
    }
    videoHistoryTasks.value = [...merged.values()];
    videoHistoryAgentPlans.value = agentResult.items;
    videoHistoryCursor.value = taskResult.has_more ? taskResult.next_cursor || null : null;
  } catch (error) {
    if (!videoHistoryDisposed) videoHistoryError.value = error instanceof Error ? error.message : "读取视频记录失败";
  } finally {
    videoHistoryLoading.value = false;
    videoHistoryLoadingMore.value = false;
  }
}
function switchHistoryType(value: "image" | "video" | "audio") {
  historyType.value = value;
  const nextQuery = { ...route.query, type: value === "image" ? undefined : value };
  void router.replace({ path: route.path, query: nextQuery });
  if (value === "video") void loadVideoHistory();
}

async function openVideoHistoryConversation(conversation: VideoHistoryConversation) {
  historyOpen.value = false;
  if (conversation.kind === "agent") {
    await router.push({ path: "/video-generation/agent", query: { conversation: conversation.id } });
    return;
  }
  const taskId = String(conversation.taskId || "").trim();
  if (!taskId) return;
  const conversationId = conversation.isLegacy ? "" : conversation.id;
  await router.push({
    path: "/video-generation",
    query: conversationId ? { conversation: conversationId } : { task: taskId },
  });
}

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
    delete nextQuery.type;
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
watch(() => route.query.type, (value) => {
  const next = value === "video" ? "video" : value === "audio" ? "audio" : "image";
  if (historyType.value === next) return;
  historyType.value = next;
  if (next === "video") void loadVideoHistory();
});
watch(historyOpen, (open) => {
  if (open && historyType.value === "video") void loadVideoHistory();
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
onBeforeUnmount(() => { videoHistoryDisposed = true; });
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

  <BaseModal :open="historyOpen" title="生成记录" width-class="max-w-[620px]" @close="closeHistory">
    <div class="h-[min(72dvh,680px)] p-5">
      <div class="mb-4 inline-flex rounded-xl border border-black/[0.06] bg-slate-100 p-1 dark:border-white/10 dark:bg-white/[0.05]" role="tablist" aria-label="生成记录类型">
        <button type="button" role="tab" :aria-selected="historyType === 'image'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="historyType === 'image' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchHistoryType('image')">
          <ImageIcon class="size-4" />
          图片生成
        </button>
        <button type="button" role="tab" :aria-selected="historyType === 'video'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="historyType === 'video' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchHistoryType('video')">
          <Film class="size-4" />
          视频记录
        </button>
        <button type="button" role="tab" :aria-selected="historyType === 'audio'" class="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold transition" :class="historyType === 'audio' ? 'bg-white text-slate-950 shadow-sm dark:bg-white/[0.12] dark:text-white' : 'text-slate-500 hover:text-slate-900 dark:text-stone-400 dark:hover:text-white'" @click="switchHistoryType('audio')">
          <AudioLines class="size-4" />
          音频记录
        </button>
      </div>

      <HistoryPanel
        v-if="historyType === 'image'"
        :conversations="conversations"
        :loading="isLoadingHistory"
        :loading-more="isLoadingMoreHistory"
        :has-more="hasMoreHistory"
        :total="historyTotal"
        :selected-id="selectedConversationId"
        :format-time="workspace.formatConversationTime"
        compact
        @create="workspace.createDraft(); closeHistory()"
        @clear="workspace.requestClearHistory"
        @select="workspace.selectConversation($event); closeHistory()"
        @remove="workspace.requestDeleteConversation"
        @delete-many="workspace.requestDeleteConversations"
        @rename="workspace.renameConversation"
        @load-more="workspace.loadMoreHistory"
      />

      <div v-else-if="historyType === 'video'" class="h-full min-h-0 overflow-y-auto pr-1">
        <p v-if="videoHistoryError" role="alert" class="mb-3 text-sm text-rose-600 dark:text-rose-300">{{ videoHistoryError }}</p>
        <div v-if="videoHistoryLoading" class="space-y-3">
          <div v-for="index in 5" :key="index" class="studio-skeleton h-[86px] rounded-xl" />
        </div>
        <div v-else-if="!videoHistoryConversations.length && !videoHistoryError" class="grid min-h-[300px] place-items-center rounded-xl border border-dashed border-slate-300 px-6 text-center dark:border-white/10">
          <div>
            <Film class="mx-auto size-8 text-[#4F7CFF]" />
            <p class="mt-3 text-sm font-semibold text-slate-900 dark:text-stone-100">暂无视频记录</p>
            <p class="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">视频生成任务和智能体对话会出现在这里。</p>
          </div>
        </div>
        <div v-else class="space-y-2">
          <article v-for="conversation in videoHistoryConversations" :key="conversation.key" class="rounded-xl border border-black/[0.06] bg-white transition hover:border-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.04]">
            <div class="flex items-stretch gap-1 p-1">
              <button type="button" class="group flex min-w-0 flex-1 items-start gap-3 rounded-lg p-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/30" :aria-label="`打开${conversation.kind === 'agent' ? '视频智能体' : '视频生成'}会话 ${conversation.title}`" @click="openVideoHistoryConversation(conversation)">
                <div class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[#4F7CFF]/10 text-[#315be8] transition group-hover:bg-[#4F7CFF]/15">
                  <component :is="conversation.kind === 'agent' ? Bot : Film" class="size-4" />
                </div>
                <div class="min-w-0 flex-1">
                  <p class="line-clamp-2 text-sm font-semibold text-slate-800 dark:text-stone-100">{{ conversation.title }}</p>
                  <div class="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-slate-500 dark:text-stone-400">
                    <span>{{ conversation.modelLabel }}</span>
                    <span v-for="item in videoHistoryConversationMeta(conversation)" :key="`${conversation.id}-${item}`" :class="item.startsWith('花费') ? 'font-semibold text-[#315be8]' : ''">{{ item }}</span>
                  </div>
                  <p v-if="conversation.deleteReason" class="mt-1 text-xs text-amber-700 dark:text-amber-300">{{ conversation.deleteReason }}</p>
                </div>
                <ChevronRight class="mt-3 size-4 shrink-0 text-slate-400 transition group-hover:text-[#315be8]" />
              </button>
              <button
                type="button"
                class="studio-button inline-flex size-10 shrink-0 self-center items-center justify-center rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/30 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-rose-400/10"
                :class="{ 'cursor-wait': deletingVideoHistoryKey === conversation.key }"
                :disabled="Boolean(conversation.deleteReason) || Boolean(deletingVideoHistoryKey)"
                :aria-label="conversation.deleteReason || `删除${conversation.kind === 'agent' ? '视频智能体' : '视频生成'}会话 ${conversation.title}`"
                :title="conversation.deleteReason || '永久删除此记录'"
                data-testid="video-history-delete"
                @click="requestDeleteVideoHistory(conversation)"
              >
                <LoaderCircle v-if="deletingVideoHistoryKey === conversation.key" class="size-4 animate-spin" />
                <Trash2 v-else class="size-4" />
              </button>
            </div>
          </article>
        </div>
        <button v-if="videoHistoryCursor || videoHistoryError" type="button" class="studio-button mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm" :disabled="videoHistoryLoading || videoHistoryLoadingMore" @click="loadVideoHistory(Boolean(videoHistoryCursor))">
          <LoaderCircle v-if="videoHistoryLoadingMore" class="size-4 animate-spin" />
          <ArrowDown v-else class="size-4" />
          {{ videoHistoryError ? '重试加载' : '加载更早的视频记录' }}
        </button>
      </div>
      <div v-else class="h-full min-h-0 overflow-y-auto pr-1">
        <AudioTaskHistoryPanel :active="historyOpen && historyType === 'audio'" />
      </div>
    </div>
  </BaseModal>

  <BaseModal :open="Boolean(videoHistoryDeleteTarget)" title="永久删除视频记录" :description="videoHistoryDeleteDescription" width-class="max-w-[460px]" :show-close="false" @close="cancelDeleteVideoHistory">
    <div class="flex justify-end gap-2 p-5">
      <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10" :disabled="Boolean(deletingVideoHistoryKey)" @click="cancelDeleteVideoHistory">取消</button>
      <button type="button" class="studio-button inline-flex min-w-24 items-center justify-center gap-1.5 rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:cursor-wait disabled:opacity-60" :disabled="Boolean(deletingVideoHistoryKey)" @click="confirmDeleteVideoHistory">
        <LoaderCircle v-if="deletingVideoHistoryKey" class="size-4 animate-spin" />
        {{ deletingVideoHistoryKey ? '删除中' : '确认删除' }}
      </button>
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
  grid-template-columns: minmax(0, 1fr);
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
  min-width: 0;
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
  min-width: 0;
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
