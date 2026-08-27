<script setup lang="ts">
import { Archive, Brain, Check, ChevronRight, CircleHelp, Clock3, Copy, Download, Edit3, Image, Lightbulb, LoaderCircle, MessageSquareText, RefreshCw, Sparkles, Trash2, Undo2, Video, XCircle } from "@lucide/vue";
import { computed, ref, watch } from "vue";
import { toast } from "vue-sonner";

import { deleteAgentMemory, downloadImageTaskZip } from "@/lib/api";
import { formatImageModel } from "@/lib/image-models";
import { extractUserDisplayPrompt } from "@/lib/prompt-display";
import type { ImageConversation, ImageTurn, StoredAgentVideo, StoredImage, StoredReferenceImage } from "@/stores/image-conversations";
import AgentMessageContent from "@/components/image/AgentMessageContent.vue";
import AgentProgressPanel from "@/components/image/AgentProgressPanel.vue";
import ImageGenerationOverlay from "@/components/image/ImageGenerationOverlay.vue";

const props = withDefaults(defineProps<{
  conversation: ImageConversation | null;
  allowTimeoutRetryContinue?: boolean;
  timeoutRetry?: { taskId: string; taskError: string } | null;
  userName?: string;
  userInitial?: string;
  userAvatarUrl?: string;
  formatConversationTime: (value: string) => string;
}>(), { conversation: null, allowTimeoutRetryContinue: false, timeoutRetry: null, userName: "用户", userInitial: "U", userAvatarUrl: "" });

const emit = defineEmits<{
  openLightbox: [images: Array<{ id: string; src: string; name?: string }>, index: number];
  continueEdit: [image: StoredImage | StoredReferenceImage];
  deletePrompt: [turnId: string];
  deleteResults: [turnId: string];
  reuseTurnConfig: [turnId: string];
  regenerateTurn: [turnId: string];
  retryImage: [turnId: string, imageId: string];
  retryBatchItem: [turnId: string, itemId: number];
  cancelTurn: [turnId: string];
  timeoutRetryContinue: [];
  timeoutRetryCancel: [];
  dismissErrors: [turnId: string];
  resumeAgent: [turnId: string, message?: string];
  openMemory: [];
  memoryChanged: [delta: number];
}>();

const downloadingTurnId = ref<string | null>(null);
const expandedTurnIds = ref<Set<string>>(new Set());
const dismissedMemoryIds = ref<Set<string>>(new Set());
const undoingMemoryId = ref<string | null>(null);
const displayUserName = computed(() => props.userName.trim() || "用户");
const displayUserInitial = computed(() => {
  const source = props.userInitial.trim() || displayUserName.value;
  return source.slice(0, 1).toUpperCase() || "U";
});
const FULL_TURN_WINDOW = 6;
const COLLAPSED_PREVIEW_LIMIT = 4;
const collapsedTurnIds = computed(() => {
  const turns = props.conversation?.turns || [];
  if (turns.length <= FULL_TURN_WINDOW) return new Set<string>();
  const keepFrom = Math.max(0, turns.length - FULL_TURN_WINDOW);
  return new Set(
    turns.flatMap((turn, index) => {
      const hasTimeoutAction = Boolean(props.timeoutRetry && turn.images.some((image) => image.taskId === props.timeoutRetry?.taskId));
      const isActive = turn.status === "planning" || turn.status === "waiting_for_input" || turn.status === "queued" || turn.status === "generating" || hasTimeoutAction;
      return !isActive && index < keepFrom ? [turn.id] : [];
    }),
  );
});
watch(() => props.conversation?.id, () => {
  expandedTurnIds.value = new Set();
  dismissedMemoryIds.value = new Set();
});

function imageSrc(image: StoredImage) {
  return image.b64_json ? `data:image/png;base64,${image.b64_json}` : image.url || "";
}
function referenceSrc(image: StoredReferenceImage) {
  return image.dataUrl || image.url || "";
}
function imageItems(turn: ImageTurn) {
  return turn.images.filter((image) => image.status === "success" && imageSrc(image)).map((image) => ({ id: image.id, src: imageSrc(image), name: image.sourceName || `${image.id}.png` }));
}
function referenceItems(turn: ImageTurn) {
  return turn.referenceImages
    .map((image, index) => ({ id: `reference-${turn.id}-${index}`, src: referenceSrc(image), name: image.name || `reference-${index + 1}.png` }))
    .filter((item) => item.src);
}
function formatVideoSize(bytes: number) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${value} B`;
}
function videoStatusLabel(video: StoredAgentVideo) {
  if (video.analysisStatus === "ready" || video.status === "ready") return "已解析";
  if (video.analysisStatus === "failed" || video.status === "failed") return "解析失败";
  if (video.analysisStatus === "queued" || video.status === "queued") return "排队中";
  if (video.analysisStatus === "processing" || video.status === "processing") return "解析中";
  return "待解析";
}
function openReferenceLightbox(turn: ImageTurn, index: number) {
  const items = referenceItems(turn);
  if (!items.length) return;
  emit("openLightbox", items, Math.max(0, Math.min(index, items.length - 1)));
}
function successImages(turn: ImageTurn) {
  return turn.images.filter((image) => image.status === "success" && imageSrc(image));
}
function agentProposal(turn: ImageTurn) {
  return turn.agentProposal?.pages?.length ? turn.agentProposal : null;
}
function proposalDetails(page: NonNullable<ImageTurn["agentProposal"]>["pages"][number]) {
  return [page.scene, page.background, page.composition, page.lighting].filter((item): item is string => Boolean(item?.trim())).slice(0, 3);
}
function folderCategorySummary(turn: ImageTurn) {
  const categories = turn.agentProposal?.folderSummary?.categories;
  if (!categories || typeof categories !== "object") return [];
  return Object.entries(categories).map(([name, count]) => `${name} ${Number(count) || 0} 张`).slice(0, 6);
}
function hasAgentProposal(turn: ImageTurn) {
  return Boolean(agentProposal(turn));
}
function agentAdvisor(turn: ImageTurn) {
  if (turn.agentAdvisor?.assistantMessage || turn.agentAdvisor?.suggestions?.length || turn.agentAdvisor?.memoryUpdates?.length) return turn.agentAdvisor;
  const proposal = agentProposal(turn);
  if (!proposal?.assistantMessage && !proposal?.suggestions?.length) return null;
  return {
    assistantMessage: proposal.assistantMessage || "",
    suggestions: proposal.suggestions || [],
    creativeBrief: proposal.creativeBrief,
  };
}
function creativeBriefItems(turn: ImageTurn) {
  const brief = agentAdvisor(turn)?.creativeBrief;
  if (!brief) return [];
  const labels: Array<[keyof typeof brief, string]> = [
    ["scene", "场景"],
    ["background", "背景"],
    ["camera", "镜头"],
    ["lighting", "光线"],
    ["visualHook", "记忆点"],
  ];
  return labels.flatMap(([key, label]) => brief[key] ? [`${label}：${brief[key]}`] : []).slice(0, 4);
}
function memorySources(turn: ImageTurn) {
  return agentAdvisor(turn)?.memorySources || [];
}
function memoryUpdates(turn: ImageTurn) {
  return (agentAdvisor(turn)?.memoryUpdates || []).filter((item) => !dismissedMemoryIds.value.has(item.memoryId));
}
async function undoMemory(memoryId: string) {
  if (!memoryId || undoingMemoryId.value) return;
  undoingMemoryId.value = memoryId;
  try {
    await deleteAgentMemory(memoryId);
    dismissedMemoryIds.value = new Set([...dismissedMemoryIds.value, memoryId]);
    emit("memoryChanged", -1);
    toast.success("已撤销这条记忆");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "撤销记忆失败");
  } finally {
    undoingMemoryId.value = null;
  }
}
function clarificationQuestion(turn: ImageTurn) {
  const question = turn.agentRun?.waitingForInput || String(turn.agentRun?.result?.promptPlan?.clarificationQuestion || "");
  return question.trim() || "请补充商品、目标平台、图片类型、图片数量、文案或修改范围。";
}
function isProfessionalConsultation(turn: ImageTurn) {
  return turn.agentAdvisor?.intent === "consult" || turn.agentAdvisor?.recommendedAction === "consult";
}
function isAgentTurn(turn: ImageTurn) {
  return turn.agentRequested === true || turn.promptEngine?.mode === "professional" || Boolean(turn.agentRun);
}
function batchFailedItems(turn: ImageTurn) {
  return (turn.agentBatchItems || []).filter((item) => item.status === "error" || item.status === "canceled");
}
function hasImageActivity(turn: ImageTurn) {
  return turn.images.length > 0;
}
function previewImages(turn: ImageTurn) {
  return successImages(turn).slice(-COLLAPSED_PREVIEW_LIMIT);
}
function isTurnCollapsed(turn: ImageTurn) {
  return collapsedTurnIds.value.has(turn.id) && !expandedTurnIds.value.has(turn.id);
}
function toggleTurnExpanded(turnId: string) {
  const next = new Set(expandedTurnIds.value);
  if (next.has(turnId)) next.delete(turnId);
  else next.add(turnId);
  expandedTurnIds.value = next;
}
function elapsed(image: StoredImage) {
  if (typeof image.elapsedSecs === "number") return `${image.elapsedSecs.toFixed(1)}s`;
  if (image.startTime) return "generation";
  return "等待中";
}
function statusLabel(turn: ImageTurn) {
  if (turn.status === "planning") return "规划中";
  if (turn.status === "waiting_for_input") return hasAgentProposal(turn) ? "等待确认" : isProfessionalConsultation(turn) ? "专业咨询" : "等待补充";
  if (turn.status === "queued") return "排队中";
  if (turn.status === "generating") return "生成中";
  if (turn.status === "success") return isAgentTurn(turn) && !hasImageActivity(turn) ? "回答完成" : "已完成";
  if (turn.status === "canceled") return "已中止";
  return "有失败";
}
function statusDetail(turn: ImageTurn) {
  if (turn.error) return turn.error;
  if (turn.status === "success") {
    const imageCount = successImages(turn).length;
    if (imageCount) return `${imageCount} 张图片已保存到图库`;
    return isAgentTurn(turn) ? "本轮已完成" : "任务完成";
  }
  if (turn.status === "waiting_for_input") {
    if (hasAgentProposal(turn)) return "方案已就绪，等待确认";
    return isProfessionalConsultation(turn) ? "等待你的回复" : "等待补充信息";
  }
  if (turn.status === "planning") return "正在理解需求";
  if (turn.status === "queued") return "任务正在排队";
  if (turn.status === "generating") return "正在生成图片";
  if (turn.status === "canceled") return "任务已中止";
  return "任务处理失败";
}
function collapsedSummary(turn: ImageTurn) {
  if (!hasImageActivity(turn)) return isAgentTurn(turn) ? statusLabel(turn) : "无图片结果";
  return `${statusLabel(turn)} · ${successImages(turn).length}/${turn.images.length} 张`;
}
function turnCost(turn: ImageTurn) {
  let hasCost = false;
  const total = turn.images.reduce((sum, image) => {
    if (typeof image.cost !== "number" || !Number.isFinite(image.cost)) return sum;
    hasCost = true;
    return sum + image.cost;
  }, 0);
  return hasCost ? total : undefined;
}
function formatCost(value: number) {
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return `￥${formatted}`;
}
function costLabel(turn: ImageTurn) {
  const cost = turnCost(turn);
  return typeof cost === "number" ? `费用 ${formatCost(cost)}` : "";
}
function repeatActionLabel(turn: ImageTurn) {
  if (hasImageActivity(turn)) return "重新生成";
  if (hasAgentProposal(turn)) return "重新规划";
  return isAgentTurn(turn) ? "重新回答" : "重新生成";
}
function deleteActionLabel(turn: ImageTurn) {
  if (hasImageActivity(turn)) return "删除结果";
  if (hasAgentProposal(turn)) return "删除方案";
  return isAgentTurn(turn) ? "删除回答" : "删除结果";
}
function statusClass(turn: ImageTurn) {
  if (turn.status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300";
  if (turn.status === "error") return "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
  if (turn.status === "canceled") return "bg-slate-100 text-slate-600 dark:bg-white/[0.08] dark:text-stone-300";
  if (turn.status === "waiting_for_input") return "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300";
  return "bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]";
}
function modelLabel(model: string) {
  return formatImageModel(model);
}
function displayPrompt(turn: ImageTurn) {
  return turn.sourcePrompt || extractUserDisplayPrompt(turn.prompt) || turn.prompt;
}
function displaySceneName(sceneName: string) {
  return sceneName
    .replace(/CowAgent open workflow/gi, "智能体自动规划")
    .replace(/CowAgent/gi, "专业模式");
}
function downloadImage(image: StoredImage) {
  const src = imageSrc(image);
  if (!src) return;
  const link = document.createElement("a");
  link.href = src;
  link.download = image.sourceName || `${image.id}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
async function downloadZip(turn: ImageTurn) {
  if (downloadingTurnId.value) return;
  const images = turn.images.filter(
    (image) => image.status === "success" && image.taskId,
  );
  if (!images.length) {
    toast.error("当前没有可下载的图片");
    return;
  }

  const folderName = `${props.conversation?.title || "image-task"}-${turn.id}`;
  downloadingTurnId.value = turn.id;
  try {
    const blob = await downloadImageTaskZip({
      folderName,
      items: images.map((image, index) => ({
        taskId: image.taskId!,
        filename: `${String(index + 1).padStart(2, "0")}-${image.sourceName || "image.png"}`,
      })),
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${folderName}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.success(`已打包 ${images.length} 张图片`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "打包下载失败");
  } finally {
    downloadingTurnId.value = null;
  }
}
function copyPrompt(prompt: string) {
  void navigator.clipboard?.writeText(prompt);
}
</script>

<template>
  <div v-if="conversation" class="image-results-thread mx-auto flex w-full flex-col gap-5 pb-2">
    <div class="mx-auto rounded-full bg-white px-3 py-1.5 text-xs font-medium text-slate-500 shadow-sm dark:bg-white/[0.08] dark:text-stone-300">
      {{ conversation.turns.length }} 轮 · {{ formatConversationTime(conversation.updatedAt) }}
    </div>

    <template v-for="turn in conversation.turns" :key="turn.id">
      <article v-if="isTurnCollapsed(turn) && !turn.resultsDeleted" class="chat-message-row chat-message-row--assistant">
        <div class="chat-avatar mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-black/[0.06] dark:bg-white/[0.08] dark:ring-white/10 sm:size-9" aria-label="家可美头像">
          <img src="/jiakemei-mark.svg" alt="" class="size-5 rounded-lg sm:size-6" loading="lazy" decoding="async" />
        </div>
        <button type="button" class="chat-message-body chat-message-body--assistant rounded-2xl rounded-tl-md border border-dashed border-black/[0.08] bg-white px-4 py-3 text-left text-sm text-slate-600 shadow-sm transition hover:border-[#4F7CFF]/25 hover:bg-[#F8FAFC] dark:border-white/10 dark:bg-[#171a21] dark:text-stone-300 dark:hover:bg-white/[0.06]" @click="toggleTurnExpanded(turn.id)">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="font-semibold text-slate-800 dark:text-stone-100">已折叠较早轮次</span>
            <span class="text-xs text-slate-400">{{ collapsedSummary(turn) }}</span>
          </div>
          <div v-if="previewImages(turn).length" class="mt-3 flex gap-2 overflow-hidden">
            <img v-for="image in previewImages(turn)" :key="image.id" :src="imageSrc(image)" :alt="image.sourceName || '生成图片预览'" class="size-12 shrink-0 rounded-xl object-cover" loading="lazy" decoding="async" />
          </div>
        </button>
      </article>

      <template v-else>
      <article class="chat-message-row chat-message-row--user" data-testid="chat-user-message">
        <div class="chat-message-body chat-message-body--user rounded-2xl rounded-tr-md bg-slate-950 px-4 py-3 text-white shadow-sm dark:bg-white dark:text-slate-950 sm:max-w-[min(720px,84%)]">
          <p v-if="turn.prompt" class="whitespace-pre-wrap text-sm leading-6">{{ displayPrompt(turn) }}</p>
          <div v-else class="text-sm text-white/60 dark:text-slate-500">提示词已删除</div>
          <div v-if="turn.promptEngine && turn.prompt && hasImageActivity(turn)" class="mt-2 inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-medium text-white/75 dark:bg-slate-950/10 dark:text-slate-500">
            <Sparkles class="size-3" />
            专业 Prompt · {{ displaySceneName(turn.promptEngine.sceneName) }}
          </div>
          <div v-if="turn.referenceImages.length" class="mt-3 flex gap-2 overflow-x-auto">
            <button
              v-for="(image, index) in turn.referenceImages"
              :key="`${image.name}-${referenceSrc(image).slice(-12)}-${index}`"
              type="button"
              class="group/reference relative size-14 shrink-0 overflow-hidden rounded-xl border border-white/15 bg-white/5 outline-none transition hover:border-white/35 focus-visible:ring-2 focus-visible:ring-white/45"
              :aria-label="`放大查看参考图 ${index + 1}`"
              @click="openReferenceLightbox(turn, index)"
            >
              <img :src="referenceSrc(image)" alt="参考图" class="h-full w-full cursor-zoom-in object-cover transition duration-200 group-hover/reference:scale-[1.04]" loading="lazy" decoding="async" />
            </button>
          </div>
          <div v-if="turn.agentVideos?.length" class="mt-3 flex flex-wrap gap-2">
            <div
              v-for="(video, index) in turn.agentVideos"
              :key="video.videoId || `${video.name}-${index}`"
              class="inline-flex min-w-0 max-w-full items-center gap-2 rounded-lg bg-white/10 px-2.5 py-2 text-[11px] text-white/75 dark:bg-slate-950/10 dark:text-slate-500"
            >
              <Video class="size-3.5 shrink-0" />
              <span class="min-w-0 truncate font-semibold" :title="video.name">{{ video.name }}</span>
              <span class="shrink-0 text-white/50 dark:text-slate-500" :title="video.analysisError || ''">{{ formatVideoSize(video.size) }} · {{ videoStatusLabel(video) }}</span>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/10 pt-2 text-[11px] text-white/60 dark:border-slate-900/10 dark:text-slate-500">
            <span v-if="hasImageActivity(turn) || !isAgentTurn(turn)">{{ turn.mode === 'edit' ? '图生图' : '文生图' }} · {{ modelLabel(turn.model) }} · {{ turn.count }} 张 · {{ turn.size }}</span>
            <span v-else>智能体对话</span>
            <span class="flex items-center gap-1">
              <button type="button" class="chat-icon-action inline-flex size-9 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="复制内容" aria-label="复制内容" @click="copyPrompt(displayPrompt(turn))">
                <Copy class="size-3.5" />
              </button>
              <button type="button" class="chat-icon-action inline-flex size-9 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="复用内容" aria-label="复用内容" @click="emit('reuseTurnConfig', turn.id)">
                <RefreshCw class="size-3.5" />
              </button>
              <button type="button" class="chat-icon-action inline-flex size-9 items-center justify-center rounded-lg hover:bg-white/10 dark:hover:bg-slate-950/10" title="删除消息" aria-label="删除消息" @click="emit('deletePrompt', turn.id)">
                <Trash2 class="size-3.5" />
              </button>
            </span>
          </div>
        </div>
        <div class="chat-avatar mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-slate-950 text-xs font-semibold text-white shadow-sm ring-1 ring-white/60 dark:bg-white dark:text-slate-950 dark:ring-white/10 sm:size-9" :title="displayUserName" aria-label="用户头像">
          <img v-if="props.userAvatarUrl" :src="props.userAvatarUrl" alt="" class="h-full w-full object-cover" loading="lazy" decoding="async" />
          <span v-else>{{ displayUserInitial }}</span>
        </div>
      </article>

      <article
        v-if="!turn.resultsDeleted"
        class="chat-message-row chat-message-row--assistant"
        :class="{ 'is-professional-turn': isAgentTurn(turn) }"
        :data-testid="isAgentTurn(turn) ? 'agent-advisor-message' : 'standard-assistant-message'"
      >
        <div class="chat-avatar mt-1 grid size-8 shrink-0 place-items-center overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-black/[0.06] dark:bg-white/[0.08] dark:ring-white/10 sm:size-9" aria-label="家可美头像">
          <img src="/jiakemei-mark.svg" alt="" class="size-5 rounded-lg sm:size-6" loading="lazy" decoding="async" />
        </div>
        <div
          class="chat-message-body chat-message-body--assistant"
          :class="isAgentTurn(turn)
            ? 'agent-response-shell'
            : 'rounded-2xl rounded-tl-md border border-black/[0.06] bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#171a21] sm:p-4'"
        >
          <div
            :class="{ 'agent-response-card': isAgentTurn(turn) }"
            :data-testid="isAgentTurn(turn) ? 'agent-response-card' : undefined"
          >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-full px-2.5 py-1 text-[11px] font-semibold" :class="statusClass(turn)">{{ statusLabel(turn) }}</span>
              <span v-if="costLabel(turn)" class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-600 dark:bg-white/[0.08] dark:text-stone-300">{{ costLabel(turn) }}</span>
              <span class="inline-flex items-center gap-1 text-xs text-slate-500 dark:text-stone-400">
                <Clock3 class="size-3.5" />
                {{ statusDetail(turn) }}
              </span>
            </div>
            <button v-if="turn.status === 'planning' || turn.status === 'waiting_for_input' || turn.status === 'queued' || turn.status === 'generating'" type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:bg-rose-400/10 dark:text-rose-300" @click="emit('cancelTurn', turn.id)">
              <XCircle class="size-3.5" />
              中止
            </button>
          </div>

          <AgentProgressPanel v-if="isAgentTurn(turn) && turn.agentRun" :run="turn.agentRun" />

          <section v-if="isAgentTurn(turn) && (turn.agentBatchProgress || turn.agentBatchItems?.length)" class="mt-4 border-y border-black/[0.06] py-3 dark:border-white/10" data-testid="agent-batch-progress">
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span class="font-semibold text-slate-800 dark:text-stone-100">文件夹批处理</span>
              <span v-if="turn.agentBatchProgress" class="text-slate-500 dark:text-stone-400">
                {{ turn.agentBatchProgress.completed }}/{{ turn.agentBatchProgress.total }} 已完成<span v-if="turn.agentBatchProgress.failed">，失败 {{ turn.agentBatchProgress.failed }} 张</span>
              </span>
            </div>
            <div v-if="turn.agentBatchProgress" class="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10">
              <div class="h-full rounded-full bg-[#315be8] transition-all" :style="{ width: `${Math.min(100, Math.round((turn.agentBatchProgress.completed / Math.max(1, turn.agentBatchProgress.total)) * 100))}%` }" />
            </div>
            <div v-if="batchFailedItems(turn).length" class="mt-2 flex flex-wrap items-center gap-2">
              <span class="text-[11px] text-rose-600 dark:text-rose-300">有图片生成失败</span>
              <button
                v-for="item in batchFailedItems(turn).slice(0, 12)"
                :key="item.id"
                type="button"
                class="studio-button inline-flex items-center gap-1 rounded-lg border border-rose-200 px-2 py-1 text-[11px] font-medium text-rose-700 hover:bg-rose-50 dark:border-rose-300/20 dark:text-rose-300 dark:hover:bg-rose-400/10"
                @click="emit('retryBatchItem', turn.id, item.id)"
              >
                <RefreshCw class="size-3" />
                重试 {{ item.name || `第 ${item.index + 1} 张` }}
              </button>
            </div>
          </section>

          <div v-if="turn.status === 'planning' && !turn.images.length" class="mt-4 flex items-center gap-3 border-y border-black/[0.06] py-4 text-sm text-slate-600 dark:border-white/10 dark:text-stone-300" data-testid="agent-planning-state">
            <LoaderCircle class="size-4 animate-spin text-[#315be8]" />
            <span>创意智能体正在理解主体、场景和视觉目标...</span>
          </div>

          <div v-if="turn.agentDialogue?.length" class="mt-4 grid gap-3 border-y border-black/[0.06] py-4 dark:border-white/10" data-testid="agent-dialogue-history">
            <div
              v-for="message in turn.agentDialogue"
              :key="message.id"
              class="flex"
              :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
            >
              <div
                class="max-w-[min(680px,88%)] rounded-xl px-3 py-2 text-xs leading-5"
                :class="message.role === 'user'
                  ? 'rounded-tr-sm bg-slate-900 text-white dark:bg-white dark:text-slate-950'
                  : 'rounded-tl-sm bg-slate-100 text-slate-700 dark:bg-white/[0.07] dark:text-stone-200'"
              >
                <AgentMessageContent v-if="message.role === 'assistant'" :content="message.content" size="small" />
                <p v-else class="whitespace-pre-wrap">{{ message.content }}</p>
                <div v-if="message.role === 'assistant' && message.knowledgeSources?.length" class="mt-1.5 flex flex-wrap gap-x-2 text-[10px] opacity-60">
                  <span v-for="source in message.knowledgeSources" :key="source.id">{{ source.title }}</span>
                </div>
              </div>
            </div>
          </div>

          <section v-if="agentProposal(turn)" class="mt-4 border-y border-black/[0.06] py-4 dark:border-white/10" data-testid="agent-proposal">
            <div v-if="agentAdvisor(turn)" class="mb-4 rounded-lg bg-amber-50/70 p-3 dark:bg-amber-400/[0.08]" data-testid="agent-advisor">
                <div class="flex items-center gap-1.5 text-xs font-semibold text-slate-800 dark:text-stone-100">
                <Lightbulb class="size-3.5 text-amber-600 dark:text-amber-300" />
                专业建议
              </div>
              <AgentMessageContent v-if="agentAdvisor(turn)?.assistantMessage" class="mt-1" :content="agentAdvisor(turn)?.assistantMessage" size="small" />
              <div v-if="creativeBriefItems(turn).length" class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] leading-4 text-slate-500 dark:text-stone-400">
                <span v-for="item in creativeBriefItems(turn)" :key="item">{{ item }}</span>
              </div>
              <div v-if="turn.agentRun?.status === 'waiting_for_input' && agentAdvisor(turn)?.suggestions?.length" class="mt-3 flex flex-wrap gap-2">
                <button
                  v-for="suggestion in agentAdvisor(turn)?.suggestions || []"
                  :key="suggestion"
                  type="button"
                  class="studio-button rounded-lg border border-black/[0.08] bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 hover:border-[#4F7CFF]/30 hover:bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200 dark:hover:bg-white/[0.08]"
                  @click="emit('resumeAgent', turn.id, suggestion)"
                >
                  {{ suggestion }}
                </button>
              </div>
            </div>
            <div class="flex items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-stone-100">
                  <Sparkles class="size-4 text-[#315be8]" />
                  {{ agentProposal(turn)?.title || '商品视觉方案' }}
                </div>
                <p class="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">{{ agentProposal(turn)?.summary }}</p>
                <div v-if="folderCategorySummary(turn).length" class="mt-2 flex flex-wrap gap-1.5 text-[10px] text-slate-500 dark:text-stone-400">
                  <span v-for="item in folderCategorySummary(turn)" :key="item" class="rounded-md bg-slate-100 px-2 py-1 dark:bg-white/[0.06]">{{ item }}</span>
                </div>
                <div v-if="agentProposal(turn)?.creativeConcept || agentProposal(turn)?.visualHook" class="mt-2 grid gap-1 text-[11px] leading-4 text-slate-600 dark:text-stone-300">
                  <p v-if="agentProposal(turn)?.creativeConcept"><span class="font-semibold text-slate-800 dark:text-stone-100">创意概念：</span>{{ agentProposal(turn)?.creativeConcept }}</p>
                  <p v-if="agentProposal(turn)?.visualHook"><span class="font-semibold text-slate-800 dark:text-stone-100">视觉记忆点：</span>{{ agentProposal(turn)?.visualHook }}</p>
                </div>
              </div>
              <span class="shrink-0 text-xs font-medium text-slate-400">{{ agentProposal(turn)?.pages.length || 0 }} 张</span>
            </div>
            <ol class="mt-3 grid gap-2 sm:grid-cols-2">
              <li v-for="(page, index) in agentProposal(turn)?.pages || []" :key="page.id" class="flex min-w-0 gap-3 rounded-lg bg-slate-50 p-2 dark:bg-white/[0.04]">
                <span class="mt-0.5 grid size-5 shrink-0 place-items-center rounded bg-[#4F7CFF]/10 text-[10px] font-semibold text-[#315be8]">{{ index + 1 }}</span>
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5 text-xs font-semibold text-slate-800 dark:text-stone-100">
                    <Image class="size-3.5" />
                    {{ page.title }}
                  </div>
                  <p class="mt-0.5 text-[11px] leading-4 text-slate-500 dark:text-stone-400">{{ page.purpose }}</p>
                  <p v-for="detail in proposalDetails(page)" :key="detail" class="mt-0.5 line-clamp-2 text-[10px] leading-4 text-slate-400 dark:text-stone-500">{{ detail }}</p>
                </div>
              </li>
            </ol>
            <div v-if="turn.agentRun?.status === 'waiting_for_input'" class="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.06] pt-3 dark:border-white/10">
              <span class="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-stone-400">
                <MessageSquareText class="size-3.5" />
                可在输入框直接说要修改哪一张
              </span>
              <button type="button" class="studio-button inline-flex items-center gap-1.5 rounded-lg bg-slate-950 px-3.5 py-2 text-xs font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-stone-200" data-testid="confirm-agent-proposal" @click="emit('resumeAgent', turn.id, '确认，开始生成')">
                <Sparkles class="size-3.5" />
                确认并生成
              </button>
            </div>
          </section>

          <section v-else-if="turn.agentRun?.status === 'waiting_for_input'" class="mt-4 border-y border-black/[0.06] py-4 dark:border-white/10" data-testid="agent-clarification">
            <div v-if="isProfessionalConsultation(turn)" class="flex gap-3" data-testid="agent-consultation">
              <span class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]">
                <Lightbulb class="size-4" />
              </span>
              <div class="min-w-0 flex-1">
                <div v-if="!isAgentTurn(turn)" class="text-sm font-semibold text-slate-900 dark:text-stone-100">专业模式</div>
                <AgentMessageContent class="mt-1" :content="agentAdvisor(turn)?.assistantMessage || clarificationQuestion(turn)" />
                <div v-if="agentAdvisor(turn)?.knowledgeSources?.length" class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400 dark:text-stone-500" :class="{ 'agent-knowledge-strip': isAgentTurn(turn) }">
                  <span v-for="source in agentAdvisor(turn)?.knowledgeSources || []" :key="source.id">知识：{{ source.title }}</span>
                </div>
                <div v-if="agentAdvisor(turn)?.suggestions?.length" class="mt-3 flex flex-wrap gap-2">
                  <button
                    v-for="suggestion in agentAdvisor(turn)?.suggestions || []"
                    :key="suggestion"
                    type="button"
                    class="studio-button rounded-lg border border-black/[0.08] bg-white px-2.5 py-1.5 text-[11px] font-medium text-slate-700 hover:border-[#4F7CFF]/30 hover:bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200 dark:hover:bg-white/[0.08]"
                    @click="emit('resumeAgent', turn.id, suggestion)"
                  >
                    {{ suggestion }}
                  </button>
                </div>
              </div>
            </div>
            <div v-else class="flex gap-3">
              <span class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300">
                <CircleHelp class="size-4" />
              </span>
              <div class="min-w-0">
                <div class="text-sm font-semibold text-slate-900 dark:text-stone-100">需要补充信息</div>
                <p class="mt-1 max-w-[68ch] text-xs leading-5 text-slate-600 dark:text-stone-300">{{ clarificationQuestion(turn) }}</p>
                <p class="mt-2 text-[11px] leading-4 text-slate-500 dark:text-stone-400">直接在输入框回复，我会重新理解需求并给出图片方案。</p>
              </div>
            </div>
          </section>

          <section v-else-if="agentAdvisor(turn)?.assistantMessage && (!turn.images.length || isAgentTurn(turn))" class="mt-4 border-y border-black/[0.06] py-4 dark:border-white/10" data-testid="agent-completed-advice">
            <div class="flex gap-3">
              <span class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]">
                <Lightbulb class="size-4" />
              </span>
              <div class="min-w-0 flex-1">
                <div v-if="!isAgentTurn(turn)" class="text-sm font-semibold text-slate-900 dark:text-stone-100">专业模式</div>
                <AgentMessageContent class="mt-1" :content="agentAdvisor(turn)?.assistantMessage" />
                <div v-if="agentAdvisor(turn)?.knowledgeSources?.length" class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400 dark:text-stone-500" :class="{ 'agent-knowledge-strip': isAgentTurn(turn) }">
                  <span v-for="source in agentAdvisor(turn)?.knowledgeSources || []" :key="source.id">知识：{{ source.title }}</span>
                </div>
              </div>
            </div>
          </section>

          <div v-if="isAgentTurn(turn) && memoryUpdates(turn).length" class="mt-3 space-y-1.5" data-testid="agent-memory-updates">
            <div v-for="item in memoryUpdates(turn)" :key="item.memoryId" class="flex min-w-0 items-center gap-2 text-xs text-slate-500 dark:text-stone-400">
              <Brain class="size-3.5 shrink-0 text-[#315be8] dark:text-[#9db3ff]" />
              <span class="shrink-0 font-semibold text-slate-700 dark:text-stone-200">已记住</span>
              <span class="min-w-0 truncate">{{ item.content }}</span>
              <button type="button" class="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 font-semibold text-[#315be8] hover:bg-[#4F7CFF]/[0.08] disabled:opacity-50 dark:text-[#9db3ff]" :disabled="undoingMemoryId === item.memoryId" @click="undoMemory(item.memoryId)">
                <LoaderCircle v-if="undoingMemoryId === item.memoryId" class="size-3 animate-spin" /><Undo2 v-else class="size-3" />撤销
              </button>
            </div>
          </div>

          <button
            v-if="isAgentTurn(turn) && memorySources(turn).length"
            type="button"
            class="mt-3 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-semibold text-[#315be8] hover:bg-[#4F7CFF]/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F7CFF]/30 dark:text-[#9db3ff]"
            data-testid="agent-memory-sources"
            @click="emit('openMemory')"
          >
            <Brain class="size-3.5" />
            本次参考 {{ memorySources(turn).length }} 条长期记忆
            <ChevronRight class="size-3.5" />
          </button>

          <div v-if="turn.images.length" class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <div v-for="(image, index) in turn.images" :key="image.id" class="group relative aspect-square overflow-hidden rounded-2xl border border-black/[0.06] bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04]" data-testid="generated-image-card">
              <img v-if="image.status === 'success' && imageSrc(image)" :src="imageSrc(image)" :alt="image.sourceName || '生成图片'" class="h-full w-full cursor-zoom-in object-contain transition duration-300 group-hover:scale-[1.02]" loading="lazy" decoding="async" data-testid="generated-image" @click="emit('openLightbox', imageItems(turn), imageItems(turn).findIndex((item) => item.id === image.id))" />
              <div v-else-if="image.status === 'loading'" class="h-full">
                <ImageGenerationOverlay
                  :label="image.taskStatus === 'queued' ? '正在准备图片' : '正在创建图片'"
                  @close="emit('cancelTurn', turn.id)"
                />
              </div>
              <div v-else-if="image.status === 'error'" class="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
                <XCircle class="size-7 text-rose-500" />
                <span class="line-clamp-2 text-xs text-rose-600 dark:text-rose-300">{{ image.error || '生成失败' }}</span>
                <button type="button" class="studio-button inline-flex items-center gap-1 rounded-xl bg-rose-50 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:bg-rose-400/10 dark:text-rose-300" @click="emit('retryImage', turn.id, image.id)">
                  <RefreshCw class="size-3" />
                  重试
                </button>
              </div>
              <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-center text-slate-400">
                <XCircle class="size-7" />
                <span class="text-xs">任务已中止</span>
              </div>
              <div v-if="image.status === 'success'" class="absolute inset-x-2 bottom-2 flex items-center justify-center gap-1 opacity-0 transition group-hover:opacity-100">
                <button type="button" class="inline-flex size-8 items-center justify-center rounded-xl bg-white/90 text-slate-700 shadow-sm" title="下载" @click.stop="downloadImage(image)">
                  <Download class="size-4" />
                </button>
                <button type="button" class="inline-flex size-8 items-center justify-center rounded-xl bg-white/90 text-slate-700 shadow-sm" title="继续编辑" @click.stop="emit('continueEdit', image)">
                  <Edit3 class="size-4" />
                </button>
              </div>
              <div v-if="image.status === 'success' && image.qualityCheck" class="absolute left-2 top-2 max-w-[calc(100%-1rem)] rounded-lg bg-white/90 px-2 py-1 text-[10px] font-semibold shadow-sm backdrop-blur dark:bg-slate-950/85" :class="image.qualityCheck.status === 'passed' ? 'text-emerald-700 dark:text-emerald-300' : image.qualityCheck.status === 'failed' ? 'text-rose-700 dark:text-rose-300' : 'text-amber-700 dark:text-amber-300'" :title="image.qualityCheck.summary || '图片质检结果'">
                {{ image.qualityCheck.status === 'passed' ? '质检通过' : image.qualityCheck.status === 'failed' ? '需要修改' : '建议复核' }} · {{ image.qualityCheck.score }}
              </div>
              <div v-if="image.pageTitle" class="absolute inset-x-2 bottom-2 truncate rounded bg-slate-950/70 px-2 py-1 text-center text-[10px] font-medium text-white backdrop-blur-sm">{{ image.pageTitle }}</div>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap justify-end gap-1 border-t border-black/[0.06] pt-3 dark:border-white/10">
            <button
              v-if="turn.images.some((image) => image.status === 'success')"
              type="button"
              class="studio-button inline-flex min-w-[108px] items-center justify-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]"
              :disabled="downloadingTurnId !== null"
              :aria-busy="downloadingTurnId === turn.id"
              @click="downloadZip(turn)"
            >
              <LoaderCircle v-if="downloadingTurnId === turn.id" class="size-3.5 animate-spin" />
              <Archive v-else class="size-3.5" />
              {{ downloadingTurnId === turn.id ? '正在打包' : '下载 ZIP' }}
            </button>
            <button type="button" class="studio-button chat-action-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]" @click="emit('regenerateTurn', turn.id)">
              <RefreshCw class="size-3.5" />
              {{ repeatActionLabel(turn) }}
            </button>
            <button v-if="turn.images.some((image) => image.status === 'error')" type="button" class="studio-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:border-white/10 dark:text-stone-300 dark:hover:bg-white/[0.08]" @click="emit('dismissErrors', turn.id)">
              <Check class="size-3.5" />
              忽略失败
            </button>
            <button type="button" class="studio-button chat-action-button inline-flex items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 py-2 text-xs font-semibold text-rose-600 hover:bg-rose-50" @click="emit('deleteResults', turn.id)">
              <Trash2 class="size-3.5" />
              {{ deleteActionLabel(turn) }}
            </button>
          </div>

          <div v-if="timeoutRetry && turn.images.some((image) => image.taskId === timeoutRetry?.taskId)" class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-200">
            <span>{{ timeoutRetry.taskError }}，可以继续等待或结束该图片。</span>
            <div class="flex gap-2">
              <button v-if="allowTimeoutRetryContinue" type="button" class="rounded-xl bg-amber-600 px-3 py-1.5 font-semibold text-white" @click="emit('timeoutRetryContinue')">继续等待</button>
              <button type="button" class="rounded-xl border border-amber-300 px-3 py-1.5 font-semibold" @click="emit('timeoutRetryCancel')">结束任务</button>
            </div>
          </div>
          </div>
        </div>
      </article>
      </template>
    </template>
  </div>
</template>

<style scoped>
.image-results-thread {
  --chat-avatar-size: 2.25rem;
  --chat-gap: 0.75rem;
  --chat-side-rail: calc(var(--chat-avatar-size) + var(--chat-gap));
  max-width: min(100%, 920px);
}

.chat-message-row {
  position: relative;
  display: flex;
  align-items: flex-start;
  width: 100%;
  gap: var(--chat-gap);
  overflow: visible;
}

.chat-avatar {
  width: var(--chat-avatar-size);
  height: var(--chat-avatar-size);
}

.chat-message-body {
  min-width: 0;
}

.chat-message-row--assistant {
  justify-content: flex-start;
}

.chat-message-row--user {
  justify-content: flex-end;
}

.chat-message-body--assistant {
  width: calc(100% - var(--chat-side-rail));
}

.chat-message-body--user {
  margin-left: auto;
  width: min(720px, calc(100% - var(--chat-side-rail)));
}

.agent-response-shell {
  display: grid;
  gap: 0;
}

.agent-response-heading {
  display: flex;
  min-height: var(--chat-avatar-size);
  align-items: center;
  padding: 0 0.1rem;
}

.agent-response-title-group {
  display: grid;
  min-width: 0;
  gap: 0.08rem;
}

.agent-response-title-group strong {
  color: rgb(15 23 42);
  font-size: 0.82rem;
  font-weight: 680;
  line-height: 1.35;
}

.agent-response-title-group span {
  color: rgb(100 116 139);
  font-size: 0.66rem;
  font-weight: 500;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.agent-response-card {
  min-width: 0;
  border: 1px solid rgb(15 23 42 / 0.08);
  border-radius: 0.75rem;
  background: #fff;
  padding: 0.9rem;
}

.agent-knowledge-strip {
  width: 100%;
  border-radius: 0.5rem;
  background: rgb(15 23 42 / 0.04);
  padding: 0.5rem 0.65rem;
  color: rgb(71 85 105);
}

.chat-action-button {
  min-height: 2.25rem;
}

.dark .agent-response-title-group strong {
  color: rgb(245 245 244 / 0.94);
}

.dark .agent-response-title-group span {
  color: rgb(168 162 158 / 0.85);
}

.dark .agent-response-card {
  border-color: rgb(255 255 255 / 0.1);
  background: #171a21;
}

.dark .agent-knowledge-strip {
  background: rgb(255 255 255 / 0.06);
  color: rgb(168 162 158);
}

@media (max-width: 640px) {
  .image-results-thread {
    --chat-avatar-size: 2rem;
    --chat-gap: 0.5rem;
  }

  .chat-message-body--user {
    width: calc(100% - var(--chat-side-rail));
  }

  .agent-response-card {
    padding: 0.8rem;
  }

  .chat-message-body button {
    min-height: 2.75rem;
  }

  .chat-icon-action {
    width: 2.75rem !important;
    height: 2.75rem !important;
  }
}
</style>
