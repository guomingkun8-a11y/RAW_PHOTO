<script setup lang="ts">
import { ArrowDown, Bot, Clapperboard, LoaderCircle, Megaphone, Paintbrush, PanelLeftOpen, UserRound } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { toast } from "vue-sonner";

import DshComposer from "@/features/dsh-agent/DshComposer.vue";
import {
  createDshAgentConversation,
  deleteDshAgentConversation,
  dshAgentModelLabel,
  fetchDshAgentConversation,
  fetchDshAgentConversations,
  fetchDshAgentStatus,
  streamDshAgentMessage,
  stopDshAgentRun,
  discardDshAgentDraft,
  updateDshAgentConversationModel,
  type DshAgentActivity,
  type DshAgentConversation,
  type DshAgentMessage,
  type DshAgentStatus,
  type DshImageMedia,
} from "@/features/dsh-agent/api";
import DshRunActivity from "@/features/dsh-agent/DshRunActivity.vue";
import DshMessageContent from "@/features/dsh-agent/DshMessageContent.vue";
import DshModelSwitch from "@/features/dsh-agent/DshModelSwitch.vue";
import DshImageResults from "./DshImageResults.vue";
import DshMessageActions from "./DshMessageActions.vue";
import DshTypingIndicator from "./DshTypingIndicator.vue";
import DshStoppedRun from "./DshStoppedRun.vue";
import DshSources from "./DshSources.vue";
import DshThreadSidebar from "./DshThreadSidebar.vue";
import DshConversationMap from "./DshConversationMap.vue";
import DshTeamPanel from "./DshTeamPanel.vue";
import { fetchDshTeam, type DshTeam, type DshTeamAction } from "./api";
import { useDshImages } from "./useDshImages";
import { sessionState } from "@/stores/session";
import { useVisibilityAwareInterval } from "@/composables/useVisibilityAwareInterval";

const conversations = ref<DshAgentConversation[]>([]);
const activeId = ref("");
const messages = ref<DshAgentMessage[]>([]);
const imageModel = ref("gpt-image-2.6-flare");
const { references, uploading, uploads, dismissUpload, pollingError, upload, remove, consume, restore } = useDshImages(activeId, messages, ensureConversation);
const draft = ref("");
const draftModel = ref("");
const expertSelections = ref<Record<string, string>>({});
const skillSelections = ref<Record<string, string[]>>({});
const selectedExpertId = computed({
  get: () => expertSelections.value[activeId.value] || "",
  set: (value: string) => {
    expertSelections.value[activeId.value] = value;
  },
});
const selectedSkillIds = computed({
  get: () => (skillSelections.value[activeId.value] ?? []).filter(id => status.value?.skills?.some(item => item.id === id)),
  set: (value: string[]) => { skillSelections.value[activeId.value] = [...value]; },
});
const collaboration = ref(false);
const teamStates = ref<Record<string, DshTeam | null>>({});
const activeTeam = computed(() => teamStates.value[activeId.value] || null);
const modelChange = ref<{ conversationId: string; model: string } | null>(null);
const status = ref<DshAgentStatus | null>(null);
const loading = ref(true);
const isWelcome = computed(() => !loading.value && !messages.value.length);
const sending = ref(false);
const sendingConversationId = ref("");
const stopping = ref(false);
const discarding = ref("");
const runUserMessageId = ref("");
const mobileHistoryOpen = ref(false);
const sidebarCollapsed = ref(false);
const historyMedia = window.matchMedia("(max-width: 760px)");
const mobileHistory = ref(historyMedia.matches);
const historyOpen = computed(() => mobileHistory.value ? mobileHistoryOpen.value : !sidebarCollapsed.value);
const historyButton = ref<HTMLButtonElement>();
const streamRoot = ref<HTMLElement | null>(null);
const composerWrap = ref<HTMLElement>();
const welcomeTasks = [
  { label: "商品图设计", icon: Paintbrush, expertId: "ecommerce-design", prompt: "请帮我设计商品图片：" },
  { label: "商品营销", icon: Megaphone, expertId: "", prompt: "请帮我梳理商品卖点和营销文案：" },
  { label: "视频创作", icon: Clapperboard, expertId: "", prompt: "请帮我策划一条商品视频：" },
];
const isAdmin = computed(() => sessionState.session?.role === "admin");
const activeConversation = computed(() => conversations.value.find((item) => item.conversation_id === activeId.value) || null);
const backgroundRun = computed(() => !sending.value && ["running", "stopping"].includes(activeConversation.value?.status || ""));
const defaultModel = computed(() => status.value?.models?.some(model => model.id === "gem-3.5-flash") ? "gem-3.5-flash" : status.value?.model || "");
const activeRun = computed(() => (sending.value && sendingConversationId.value === activeId.value) || backgroundRun.value
  || messages.value.some(message => message.media?.some(media => media.kind === "task" && ["queued", "pending", "running"].includes(media.task?.status || ""))));
const selectedModel = computed(() =>
  (modelChange.value?.conversationId === activeId.value ? modelChange.value.model : activeConversation.value?.model)
    || draftModel.value || status.value?.model || "",
);
const modelOptions = computed(() => {
  const options = (status.value?.models || []).map((model) => ({ value: model.id, label: dshAgentModelLabel(model.id, status.value?.models) }));
  for (const model of [status.value?.model, selectedModel.value]) {
    if (model && !options.some((option) => option.value === model)) options.push({ value: model, label: dshAgentModelLabel(model) });
  }
  return options;
});
const modelDisabled = computed(() => loading.value || sending.value || uploading.value || !!modelChange.value || ["running", "stopping"].includes(activeConversation.value?.status || "") || !status.value?.available);
const lastAssistantId = computed(() => messages.value.slice().reverse().find((message) => message.role === "assistant")?.message_id);
const contextSnapshot = computed(() => messages.value.slice().reverse().find(message => message.context)?.context || null);
const contextModel = computed(() => status.value?.models?.find(model => model.id === selectedModel.value));
// Tasks stay bound to their originating user message for polling and idempotency.
const displayMedia = computed(() => {
  const result = new Map<string, DshImageMedia[]>(messages.value.map(message => [message.message_id, message.media || []]));
  let user: DshAgentMessage | undefined;
  for (const message of messages.value) {
    if (message.role === "user") user = message;
    else if (message.role === "assistant" && user) {
      const tasks = (user.media || []).filter(item => item.kind === "task" || item.kind === "diagram");
      result.set(user.message_id, (user.media || []).filter(item => item.kind !== "task" && item.kind !== "diagram"));
      result.set(message.message_id, [...(message.media || []), ...tasks]);
      user = undefined;
    }
  }
  return result;
});
let selectionVersion = 0;
let modelRevision = 0;
let disposed = false;
let streamController: AbortController | null = null;
let currentRun: { conversationId: string; messages: DshAgentMessage[] } | null = null;
const followOutput = ref(true);
let scrollingToLatest = false;

function collapseHistory() {
  mobileHistoryOpen.value = false;
  if (!mobileHistory.value) sidebarCollapsed.value = true;
}

function focusHistoryButton() {
  requestAnimationFrame(() => {
    if (!historyOpen.value) historyButton.value?.focus({ preventScroll: true });
  });
}

async function expandHistory() {
  if (mobileHistory.value) mobileHistoryOpen.value = true;
  else sidebarCollapsed.value = false;
  await nextTick();
  document.querySelector<HTMLButtonElement>(".dsh-sidebar-collapse")?.focus({ preventScroll: true });
}

function updateHistoryViewport(event: MediaQueryListEvent) {
  mobileHistory.value = event.matches;
  mobileHistoryOpen.value = false;
}

async function loadConversations() {
  const revision = modelRevision;
  const result = await fetchDshAgentConversations();
  if (disposed) return;
  conversations.value = result.items.map((conversation) => {
    const current = conversations.value.find((item) => item.conversation_id === conversation.conversation_id);
    return current && (revision !== modelRevision || modelChange.value?.conversationId === conversation.conversation_id)
      ? { ...conversation, model: current.model }
      : conversation;
  });
}

async function ensureConversation() {
  if (activeId.value) return activeId.value;
  const version = selectionVersion;
  const conversation = await createDshAgentConversation(selectedModel.value);
  conversations.value.unshift(conversation);
  if (!disposed && version === selectionVersion) {
    ++selectionVersion;
    expertSelections.value[conversation.conversation_id] = selectedExpertId.value;
    skillSelections.value[conversation.conversation_id] = [...selectedSkillIds.value];
    activeId.value = conversation.conversation_id;
  }
  return conversation.conversation_id;
}

function editImage(reference: string) {
  draft.value = `请修改这张图片（图片引用：${reference}）：`;
  document.querySelector<HTMLTextAreaElement>(".dsh-composer textarea")?.focus();
}

async function selectWelcomeTask(task: typeof welcomeTasks[number]) {
  if (modelDisabled.value) return;
  selectedExpertId.value = status.value?.experts?.find(expert => expert.id === task.expertId)?.id || "";
  collaboration.value = false;
  if (!draft.value.trim() || welcomeTasks.some(item => item.prompt === draft.value)) {
    draft.value = selectedExpertId.value ? "" : task.prompt;
  }
  await nextTick();
  composerWrap.value?.querySelector<HTMLTextAreaElement>("textarea")?.focus({ preventScroll: true });
}

async function selectConversation(id: string) {
  cancelLatestScroll();
  const version = ++selectionVersion;
  const revision = modelRevision;
  activeId.value = id;
  mobileHistoryOpen.value = false;
  messages.value = [];
  followOutput.value = true;
  loading.value = true;
  try {
    if (currentRun?.conversationId === id) {
      messages.value = currentRun.messages;
      await scrollToBottom();
      return;
    }
    const result = await fetchDshAgentConversation(id);
    if (disposed || version !== selectionVersion) return;
    conversations.value = conversations.value.map((item) => item.conversation_id === id
      ? { ...item, ...result.conversation, model: revision !== modelRevision || modelChange.value?.conversationId === id ? item.model : result.conversation.model || item.model }
      : item);
    // Preserve a switch that committed while this history request was in flight.
    const newNotices = messages.value.filter((message) => message.role === "model_switch"
      && !result.items.some((item) => item.message_id === message.message_id));
    messages.value = [...result.items, ...newNotices];
    if (!(id in expertSelections.value)) {
      const expert = result.items.slice().reverse().find(message => message.role === "user")?.expert_id || "";
      expertSelections.value[id] = status.value?.experts?.some(item => item.id === expert) ? expert : "";
    }
    if (!(id in skillSelections.value)) {
      const skills = result.items.slice().reverse().find(message => message.role === "user")?.skill_ids;
      if (skills) skillSelections.value[id] = skills.filter(skill => status.value?.skills?.some(item => item.id === skill));
    }
    teamStates.value[id] = result.team || null;
    collaboration.value = !!result.team;
    restore(id, result.draft_references || []);
    await scrollToBottom();
  } catch (error) {
    if (!disposed && version === selectionVersion) toast.error(error instanceof Error ? error.message : "加载对话失败");
  } finally {
    if (!disposed && version === selectionVersion) loading.value = false;
  }
}

function newConversation() {
  if (loading.value || sending.value || uploading.value || modelChange.value) return;
  cancelLatestScroll();
  ++selectionVersion;
  mobileHistoryOpen.value = false;
  activeId.value = "";
  selectedExpertId.value = "";
  selectedSkillIds.value = [];
  messages.value = [];
  collaboration.value = false;
  draftModel.value = defaultModel.value;
  followOutput.value = true;
}

async function changeModel(model: string) {
  if (modelDisabled.value || model === selectedModel.value || !modelOptions.value.some((option) => option.value === model)) return;
  if (!activeId.value) {
    draftModel.value = model;
    return;
  }
  const conversationId = activeId.value;
  ++modelRevision;
  modelChange.value = { conversationId, model };
  try {
    const { model_switch: notice, ...conversation } = await updateDshAgentConversationModel(conversationId, model);
    if (disposed) return;
    conversations.value = conversations.value.map((item) => item.conversation_id === conversationId ? conversation : item);
    if (activeId.value === conversationId && notice) {
      if (!messages.value.some((item) => item.message_id === notice.message_id)) messages.value.push(notice);
      await scrollToBottom(true);
    }
  } catch (error) {
    if (!disposed) toast.error(error instanceof Error ? error.message : "切换模型失败");
  } finally {
    ++modelRevision;
    modelChange.value = null;
  }
}

async function removeConversation(conversation: DshAgentConversation) {
  if (sending.value || modelChange.value || !window.confirm(`确定删除“${conversation.title}”及其对话记录吗？`)) return;
  try {
    await deleteDshAgentConversation(conversation.conversation_id);
    conversations.value = conversations.value.filter((item) => item.conversation_id !== conversation.conversation_id);
    if (activeId.value === conversation.conversation_id) {
      activeId.value = "";
      messages.value = [];
      if (conversations.value[0]) await selectConversation(conversations.value[0].conversation_id);
    }
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除对话失败");
  }
}

function trackMessageScroll() {
  const root = streamRoot.value;
  if (root && !scrollingToLatest) followOutput.value = root.scrollHeight - root.scrollTop - root.clientHeight < 64;
}

function jumpToLatest() {
  const root = streamRoot.value;
  if (!root) return;
  if (root.scrollHeight - root.scrollTop - root.clientHeight < 2) {
    scrollingToLatest = false;
    followOutput.value = true;
    return;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    void scrollToBottom(true);
    return;
  }
  scrollingToLatest = true;
  followOutput.value = false;
  root.scrollTo({ top: root.scrollHeight, behavior: "smooth" });
}

function finishLatestScroll() {
  // New output may have moved the destination during the native animation.
  if (scrollingToLatest) jumpToLatest();
}

function cancelLatestScroll() {
  if (!scrollingToLatest) return;
  scrollingToLatest = false;
  const root = streamRoot.value;
  root?.scrollTo({ top: root.scrollTop, behavior: "instant" });
  trackMessageScroll();
}

async function scrollToBottom(force = false) {
  await nextTick();
  if (scrollingToLatest && !force) return;
  if (streamRoot.value && (force || followOutput.value)) {
    scrollingToLatest = false;
    followOutput.value = true;
    streamRoot.value.scrollTo({ top: streamRoot.value.scrollHeight, behavior: "instant" });
  }
}

async function stopRun() {
  const conversationId = backgroundRun.value ? activeId.value : sendingConversationId.value;
  const messageId = backgroundRun.value ? messages.value.slice().reverse().find(item => item.role === "user")?.message_id : runUserMessageId.value;
  if ((!sending.value && !backgroundRun.value) || stopping.value || !messageId) return;
  stopping.value = true;
  try {
    const result = await stopDshAgentRun(conversationId, messageId);
    if (!result.accepted) stopping.value = false;
  } catch (error) {
    stopping.value = false;
    toast.error(error instanceof Error ? error.message : "停止失败，请重试");
  }
}

function retryMessage(message: DshAgentMessage, resume = false) {
  if (message.message_id !== lastAssistantId.value || modelDisabled.value) return;
  const index = messages.value.indexOf(message);
  const question = messages.value.slice(0, index).reverse().find((item) => item.role === "user");
  const content = resume
    ? `请继续上一条被中断的文字回答，从中断处接着说，不重复已有内容。\n已收到的片段：\n${message.content.slice(-9000)}`
    : `请重新回答我上一条问题，仅重新组织文字回答，不要重复生成图片。\n原问题：\n${question?.content.slice(0, 11000) || "请重新组织上一条回复"}`;
  void sendMessage(content, true);
}

async function discardMessage(message: DshAgentMessage) {
  if (discarding.value || sending.value) return;
  discarding.value = message.message_id;
  const runMessages = messages.value;
  try {
    await discardDshAgentDraft(message);
    const index = runMessages.findIndex((item) => item.message_id === message.message_id);
    if (index >= 0) runMessages.splice(index, 1);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "丢弃失败");
  } finally {
    discarding.value = "";
  }
}

function editTeamPlan() {
  collaboration.value = true;
  draft.value = "请修改当前协作计划：";
  document.querySelector<HTMLTextAreaElement>(".dsh-composer textarea")?.focus();
}

async function teamAction(action: DshTeamAction) {
  if (sending.value || !activeTeam.value) return;
  if (action === "discard" && !window.confirm("确定结束当前协作吗？已提交的图片任务不会因此取消，可能仍会计费。")) return;
  collaboration.value = true;
  await sendMessage({ approve: "确认执行当前协作计划", discard: "取消当前协作计划", resume: "继续执行原协作任务，保留已有结果" }[action], false, action);
}

async function sendMessage(override?: string, textOnly = false, action?: DshTeamAction) {
  const content = (override ?? draft.value).trim() || (!textOnly && references.value.length ? "请分析附件内容" : "");
  if (!content || sending.value || uploading.value || loading.value || modelChange.value || activeConversation.value?.status === "running") return;
  if (!status.value?.available) {
    toast.error(status.value?.enabled ? "智能体服务尚未配置完成" : "AI 智能体尚未启用");
    return;
  }

  sending.value = true;
  const model = selectedModel.value;
  const expertId = selectedExpertId.value;
  const skillIds = [...selectedSkillIds.value];
  const version = selectionVersion;
  const runMessages = messages.value;
  const runReferences = textOnly ? [] : [...references.value];
  const runImageOptions = { model: imageModel.value };
  const runCollaboration = { mode: collaboration.value ? "teams" as const : "normal" as const,
    ...(action ? { team_action: action, team_revision: activeTeam.value?.revision } : {}) };
  if (!override) draft.value = "";
  stopping.value = false;
  runUserMessageId.value = "";
  const controller = new AbortController();
  streamController = controller;
  let response: DshAgentMessage | undefined;
  try {
    let conversationId = activeId.value;
    if (!conversationId) {
      const conversation = await createDshAgentConversation(model);
      if (disposed) return;
      conversationId = conversation.conversation_id;
      conversations.value = [conversation, ...conversations.value];
      expertSelections.value[conversationId] = expertId;
      skillSelections.value[conversationId] = skillIds;
      if (version === selectionVersion) {
        ++selectionVersion;
        activeId.value = conversationId;
      }
    }
    if (disposed) return;
    if (!conversationId) throw new Error("无法创建对话");
    sendingConversationId.value = conversationId;
    const now = new Date().toISOString();
    runMessages.push({
      message_id: `local-user-${Date.now()}`, conversation_id: conversationId, role: "user", status: "completed",
      content, model: "", expert_id: expertId, skill_ids: skillIds, input_tokens: null, output_tokens: null, total_tokens: null, usage: {}, error_code: "", created_at: now, media: runReferences,
    });
    const userMessage = runMessages[runMessages.length - 1]!;
    if (!textOnly) consume(conversationId);
    const responseId = `local-assistant-${Date.now()}`;
    runMessages.push({
      message_id: responseId, conversation_id: conversationId, role: "assistant", status: "streaming",
      content: "", model, expert_id: expertId, skill_ids: skillIds, input_tokens: null, output_tokens: null, total_tokens: null,
      usage: {}, error_code: "", created_at: now,
    });
    response = runMessages[runMessages.length - 1]!;
    currentRun = { conversationId, messages: runMessages };
    const runResponse = response;
    await scrollToBottom(true);

    await streamDshAgentMessage(conversationId, content, (event) => {
      if (disposed || controller.signal.aborted) return;
      if (event.type === "start") {
        runUserMessageId.value = event.user_message_id || "";
        if (event.user_message_id) userMessage.message_id = event.user_message_id;
        runResponse.model = event.model;
        const conversation = conversations.value.find((item) => item.conversation_id === conversationId);
        if (conversation) conversation.model = event.model;
      } else if (event.type === "delta") {
        runResponse.content += event.text;
      } else if (event.type === "activity") {
        runResponse.activities = mergeActivities(runResponse.activities, [event.activity]);
      } else if (event.type === "timing") {
        runResponse.timings = event.timings;
      } else if (event.type === "context") {
        runResponse.context = event.context;
      } else if (event.type === "team") {
        teamStates.value[conversationId] = event.team;
        runResponse.team = event.team;
      } else if (event.type === "media") {
        userMessage.message_id = event.media.message_id;
        const existing = (userMessage.media || []).filter((item) => item.media_id !== event.media.media_id);
        userMessage.media = [...existing, event.media];
      } else if (event.type === "complete") {
        const activities = mergeActivities(runResponse.activities, event.message.activities);
        Object.assign(runResponse, event.message, { activities });
        void loadConversations().catch(() => {
          if (!disposed) toast.error("回复已收到，对话列表刷新失败");
        });
      }
      if (activeId.value === conversationId && (event.type === "delta" || event.type === "activity" || event.type === "timing" || event.type === "media" || event.type === "complete")) void scrollToBottom();
    }, controller.signal, model, status.value?.image_tools_enabled || status.value?.files_enabled ? {
      ...(status.value?.image_tools_enabled ? {
        reference_ids: runReferences.filter(item => item.kind === "reference").map(item => item.media_id),
        image_options: runImageOptions,
      } : {}),
      ...(status.value?.files_enabled ? { file_ids: runReferences.filter(item => item.kind === "file").map(item => item.media_id) } : {}),
    } : undefined, textOnly, runCollaboration, expertId, skillIds);
  } catch (error) {
    if (disposed || controller.signal.aborted) return;
    const detail = error instanceof Error ? error.message : "智能体回复失败";
    if (response) {
      response.status = "error";
      response.error_code = `${detail}。已保留收到的内容，可稍后重新打开对话查看结果。`;
    } else {
      if (!override) draft.value = content;
    }
    toast.error(detail);
  } finally {
    const cid = sendingConversationId.value;
    if (runCollaboration.mode === "teams" && cid && !disposed) {
      try { teamStates.value[cid] = (await fetchDshTeam(cid)).team; }
      catch { toast.error("协作状态刷新失败，请重新打开对话查看"); }
    }
    currentRun = null;
    streamController = null;
    sendingConversationId.value = "";
    sending.value = false;
    stopping.value = false;
    runUserMessageId.value = "";
  }
}

function mergeActivities(current: DshAgentActivity[] = [], updates: DshAgentActivity[] = []) {
  const result = [...current];
  for (const activity of updates) {
    const index = result.findIndex((item) => item.id === activity.id);
    if (index < 0) result.push(activity);
    else result[index] = { ...result[index]!, ...activity };
  }
  return result;
}

let statusFailures = 0;
let nextStatusPoll = 0;
const backgroundPolling = useVisibilityAwareInterval(async () => {
  if (!backgroundRun.value || Date.now() < nextStatusPoll) return;
  const cid = activeId.value;
  const version = selectionVersion;
  try {
    const state = await fetchDshTeam(cid);
    if (disposed || activeId.value !== cid || version !== selectionVersion || sending.value) return;
    teamStates.value[cid] = state.team;
    statusFailures = 0;
    if (state.status && !["running", "stopping"].includes(state.status)) {
      const conversation = conversations.value.find(item => item.conversation_id === cid);
      if (conversation) conversation.status = state.status;
      stopping.value = false;
      await selectConversation(cid);
    }
  } catch {
    statusFailures += 1;
    nextStatusPoll = Date.now() + Math.min(30000, 3000 * 2 ** Math.min(statusFailures, 4));
  }
}, 3000);
watch(backgroundRun, active => { statusFailures = 0; nextStatusPoll = 0; backgroundPolling.restart(active); }, { immediate: true });

onBeforeUnmount(() => {
  historyMedia.removeEventListener("change", updateHistoryViewport);
  disposed = true;
  ++selectionVersion;
  streamController?.abort();
});

function formatTime(value: string) {
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

onMounted(async () => {
  historyMedia.addEventListener("change", updateHistoryViewport);
  loading.value = true;
  try {
    status.value = await fetchDshAgentStatus();
    if (disposed) return;
    draftModel.value = defaultModel.value;
    if (status.value.image_model) imageModel.value = status.value.image_model;
    await loadConversations();
    if (!disposed && !activeId.value && conversations.value[0]) await selectConversation(conversations.value[0].conversation_id);
  } catch (error) {
    if (!disposed) toast.error(error instanceof Error ? error.message : "加载智能体失败");
  } finally {
    if (!selectionVersion) loading.value = false;
  }
});

watch(() => messages.value.length, () => void scrollToBottom());

watch(isWelcome, async (welcome, wasWelcome, onCleanup) => {
  if (welcome || !wasWelcome || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const composer = composerWrap.value?.querySelector<HTMLElement>(".dsh-composer");
  if (!composer) return;
  const before = composer.getBoundingClientRect();
  let canceled = false;
  let animation: Animation | undefined;
  onCleanup(() => { canceled = true; animation?.cancel(); });
  await nextTick();
  if (canceled || disposed) return;
  const after = composer.getBoundingClientRect();
  // Animate the same composer after layout settles, preserving its draft and focus.
  animation = composer.animate([
    { transform: `translateY(${before.y - after.y}px)` },
    { transform: "translateY(0)" },
  ], { duration: 280, easing: "cubic-bezier(.22, 1, .36, 1)" });
});
</script>

<template>
  <main class="dsh-page" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
    <Transition name="dsh-scrim-fade">
      <button v-if="mobileHistory && mobileHistoryOpen" class="dsh-mobile-scrim" type="button" aria-label="关闭对话历史" @click="collapseHistory" />
    </Transition>
    <Transition name="dsh-sidebar-slide" @after-leave="focusHistoryButton">
      <DshThreadSidebar v-show="historyOpen" :inert="!historyOpen" :aria-hidden="!historyOpen" :conversations="conversations" :active-id="activeId" :busy="loading || sending || uploading || !!modelChange" :loading="loading" :available="!!status?.available" :admin="isAdmin" @create="newConversation" @select="selectConversation" @remove="removeConversation" @collapse="collapseHistory" />
    </Transition>

    <section class="dsh-chat-panel" :class="{ 'is-welcome': isWelcome }">
      <header class="dsh-chat-header">
        <button ref="historyButton" class="dsh-history-button" type="button" aria-label="打开对话历史" title="展开对话侧边栏" aria-controls="dsh-thread-sidebar" :aria-expanded="historyOpen" @click="expandHistory"><PanelLeftOpen class="size-4" /></button>
        <div><strong>{{ activeConversation?.title || '新的对话' }}</strong><span>{{ dshAgentModelLabel(selectedModel, status?.models) }}</span></div>
        <span class="dsh-runtime-badge"><i />Harness</span>
      </header>

      <div v-show="!isWelcome" class="dsh-thread-body">
      <div ref="streamRoot" class="dsh-messages" aria-live="polite" @scroll="trackMessageScroll" @scrollend="finishLatestScroll" @wheel.passive="cancelLatestScroll" @touchstart.passive="cancelLatestScroll" @pointerdown="cancelLatestScroll" @keydown="cancelLatestScroll">
        <div v-if="loading" class="dsh-empty"><LoaderCircle class="size-5 animate-spin" /><span>正在加载对话</span></div>
        <template v-for="message in messages" :key="message.message_id">
          <DshModelSwitch v-if="message.role === 'model_switch' && message.model_switch" :from-model="message.model_switch.from_model" :to-model="message.model_switch.to_model" />
          <article v-else class="dsh-message" :class="message.role" :data-message-id="message.message_id">
            <span class="dsh-avatar"><UserRound v-if="message.role === 'user'" class="size-4" /><Bot v-else class="size-4" /></span>
            <div class="dsh-message-content">
              <div class="dsh-message-meta"><strong>{{ message.role === 'user' ? '你' : dshAgentModelLabel(message.model, status?.models) }}</strong><span v-if="message.expert_id" class="dsh-message-expert">{{ status?.experts?.find(item => item.id === message.expert_id)?.label }}</span><time>{{ formatTime(message.created_at) }}</time></div>
              <DshRunActivity v-if="message.role === 'assistant' && (message.activities?.length || message.status === 'streaming')" hide-sources :activities="message.activities || []" :status="message.status" :timings="message.timings" :has-content="!!message.content" :stopping="stopping && message.status === 'streaming'" />
              <DshTypingIndicator v-if="message.role === 'assistant' && message.status === 'streaming' && !message.content" />
              <DshMessageContent v-if="message.role === 'assistant' && message.content" class="dsh-message-text" :content="message.content" :streaming="message.status === 'streaming'" />
              <p v-else-if="message.content" class="dsh-message-text">{{ message.content }}</p>
              <DshImageResults v-if="displayMedia.get(message.message_id)?.length" class="dsh-message-media" :media="displayMedia.get(message.message_id)!" @edit="editImage" />
              <DshSources v-if="message.role === 'assistant'" :activities="message.activities || []" />
              <div v-if="message.role === 'assistant' && message.total_tokens" class="dsh-message-usage">{{ message.total_tokens.toLocaleString() }} tokens</div>
              <div v-if="message.status === 'error'" class="dsh-message-error" role="status">本次回复未完成：{{ message.error_code || '运行错误' }}</div>
              <DshStoppedRun v-if="message.status === 'stopped'" :disabled="modelDisabled || !!discarding" :discarding="discarding === message.message_id" :can-continue="message.message_id === lastAssistantId" :has-images="messages.some(item => item.media?.some(media => media.kind === 'task'))" @continue="retryMessage(message, true)" @discard="discardMessage(message)" />
              <DshMessageActions v-if="message.role === 'assistant' && message.status !== 'streaming'" :message="message" :can-retry="message.message_id === lastAssistantId && message.status !== 'stopped'" :busy="modelDisabled" @retry="retryMessage(message)" @feedback="message.feedback = $event" />
            </div>
          </article>
        </template>
        <DshTeamPanel v-if="activeTeam" :team="activeTeam" :busy="modelDisabled" @action="teamAction" @edit="editTeamPlan" />
      </div>
      <DshConversationMap :messages="messages" :viewport="streamRoot" />
      <button
        v-if="!loading && messages.length && (activeRun || !followOutput)"
        class="dsh-latest-button"
        type="button"
        :aria-label="activeRun && followOutput ? '正在运行，回到最新消息' : '回到最新消息'"
        :title="activeRun && followOutput ? '正在运行，点击回到最新消息' : '回到最新消息'"
        @click="jumpToLatest"
      >
        <DshTypingIndicator v-if="activeRun && followOutput" compact aria-hidden="true" />
        <ArrowDown v-else class="size-4" aria-hidden="true" />
      </button>
      </div>

      <footer ref="composerWrap" class="dsh-composer-wrap">
        <div v-if="isWelcome" class="dsh-welcome">
          <h2>今天想完成什么？</h2>
          <p v-if="status && !status.available" class="dsh-config-warning">{{ !status.enabled ? '该功能目前未启用。' : !status.configured ? '模型中转尚未配置完整。' : '排队服务暂不可用，请稍后重试。' }}</p>
        </div>
        <DshComposer
          v-model="draft"
          :welcome="isWelcome"
          :model="selectedModel"
          :options="modelOptions"
          :disabled="modelDisabled"
          :sending="sending"
          :can-stop="(sending && !!runUserMessageId && sendingConversationId === activeId) || backgroundRun"
          :stopping="stopping"
          :saving-model="!!modelChange"
          v-model:image-model="imageModel"
          :image-models="status?.image_models"
          :references="references"
          :uploading="uploading"
          :uploads="uploads"
          :files-enabled="status?.files_enabled"
          :experts="status?.experts"
          v-model:expert-id="selectedExpertId"
          :skills="status?.skills"
          v-model:skill-ids="selectedSkillIds"
          :connectors="status?.connectors"
          :context-model="contextModel"
          :context="contextSnapshot"
          :teams-enabled="status?.teams_enabled"
          :search-enabled="status?.search_enabled"
          v-model:collaboration="collaboration"
          @upload="upload"
          @remove-reference="remove"
          @dismiss-upload="dismissUpload"
          @select-model="changeModel"
          @send="sendMessage()"
          @stop="stopRun"
        />
        <nav v-if="isWelcome" class="dsh-welcome-actions" aria-label="快捷任务">
          <button v-for="task in welcomeTasks" :key="task.label" type="button" :disabled="modelDisabled" @click="selectWelcomeTask(task)">
            <component :is="task.icon" aria-hidden="true" /><span>{{ task.label }}</span>
          </button>
        </nav>
        <p v-if="pollingError" class="dsh-background-run" role="status">{{ pollingError }}</p>
        <p v-if="sending && sendingConversationId !== activeId" class="dsh-background-run" role="status">另一对话正在运行</p>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.dsh-page { --dsh-sidebar-duration: 280ms; --dsh-sidebar-ease: cubic-bezier(.22, 1, .36, 1); display: grid; height: calc(100dvh - var(--studio-nav-height)); min-height: 0; overflow: hidden; grid-template-columns: 260px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); color: var(--foreground); background: var(--studio-bg); transition: grid-template-columns var(--dsh-sidebar-duration) var(--dsh-sidebar-ease); }
.dsh-page.is-sidebar-collapsed { grid-template-columns: 0px minmax(0, 1fr); }
.dsh-sidebar-slide-enter-active, .dsh-sidebar-slide-leave-active { transition: transform var(--dsh-sidebar-duration) var(--dsh-sidebar-ease); }
.dsh-sidebar-slide-enter-from, .dsh-sidebar-slide-leave-to { transform: translateX(-100%); }
.dsh-sidebar-slide-leave-active, .dsh-scrim-fade-leave-active { pointer-events: none; }
.dsh-scrim-fade-enter-active, .dsh-scrim-fade-leave-active { transition: opacity var(--dsh-sidebar-duration) ease-out; }
.dsh-scrim-fade-enter-from, .dsh-scrim-fade-leave-to { opacity: 0; }
.dsh-chat-panel { display: grid; grid-column: 2; grid-row: 1; min-width: 0; min-height: 0; overflow: hidden; grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr) auto; }
.dsh-chat-panel.is-welcome { grid-template-rows: auto minmax(min-content, 1fr); overflow-y: auto; overscroll-behavior-y: contain; }
.dsh-chat-header { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--studio-border); padding: 0 22px; }
.dsh-chat-header > div { display: flex; min-width: 0; flex: 1; align-items: center; gap: 10px; }
.dsh-chat-header strong { overflow: hidden; font-size: 13px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.dsh-chat-header > div span { color: var(--muted-foreground); font-size: 11px; }
.dsh-runtime-badge { display: flex; align-items: center; gap: 6px; color: var(--muted-foreground); font-size: 11px; }
.dsh-runtime-badge i { width: 6px; height: 6px; border-radius: 50%; background: var(--ai-success); }
.dsh-thread-body { position: relative; min-width: 0; min-height: 0; container-type: inline-size; }
.dsh-latest-button {
  --dsh-glass-fill: rgba(255, 255, 255, .18);
  --dsh-glass-hover: rgba(255, 255, 255, .28);
  --dsh-glass-edge: rgba(255, 255, 255, .42);
  --dsh-glass-highlight: rgba(255, 255, 255, .3);
  --dsh-glass-shade: rgba(15, 23, 42, .1);
  position: absolute; z-index: 2; bottom: 12px; left: 50%;
  display: grid; width: 36px; height: 36px; place-items: center;
  transform: translateX(-50%); overflow: hidden; padding: 0;
  border: 1px solid var(--dsh-glass-edge); border-radius: 50%;
  background: var(--studio-panel); color: var(--foreground); cursor: pointer;
  box-shadow: 0 4px 12px var(--dsh-glass-shade), 0 1px 2px var(--dsh-glass-shade), inset 0 1px 1px var(--dsh-glass-highlight), inset 0 -1px 1px rgba(255, 255, 255, .12);
  transition: transform 180ms cubic-bezier(.22, 1, .36, 1), background-color 180ms ease-out, box-shadow 180ms ease-out;
}
.dark .dsh-latest-button { --dsh-glass-fill: rgba(23, 26, 33, .22); --dsh-glass-hover: rgba(35, 39, 48, .32); --dsh-glass-edge: rgba(255, 255, 255, .16); --dsh-glass-highlight: rgba(255, 255, 255, .18); --dsh-glass-shade: rgba(0, 0, 0, .24); }
.dsh-latest-button::before { content: ''; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; background: linear-gradient(140deg, var(--dsh-glass-highlight), rgba(255, 255, 255, 0) 46%, rgba(255, 255, 255, .04)); opacity: .35; }
.dsh-latest-button > * { position: relative; z-index: 1; pointer-events: none; }
@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .dsh-latest-button { background-color: var(--dsh-glass-fill); -webkit-backdrop-filter: blur(10px) saturate(125%); backdrop-filter: blur(10px) saturate(125%); }
  .dsh-latest-button:hover { --dsh-glass-fill: var(--dsh-glass-hover); }
}
@media (hover: hover) and (pointer: fine) {
  .dsh-latest-button:hover { transform: translate(-50%, -1px) scale(1.04); box-shadow: 0 6px 16px var(--dsh-glass-shade), 0 1px 3px var(--dsh-glass-shade), inset 0 1px 1px var(--dsh-glass-highlight), inset 0 -1px 1px rgba(255, 255, 255, .18); }
}
.dsh-latest-button:focus-visible { outline: 2px solid var(--ring); outline-offset: 3px; }
.dsh-latest-button:active { transform: translateX(-50%) scale(.96); }
.dsh-messages { height: 100%; min-width: 0; min-height: 0; overflow-y: auto; overscroll-behavior-y: contain; scrollbar-gutter: stable; scrollbar-width: thin; scrollbar-color: var(--input) transparent; padding: 32px 56px 36px 32px; }
.dsh-messages::-webkit-scrollbar { width: 5px; }
.dsh-messages::-webkit-scrollbar-thumb { border-radius: 4px; background: var(--input); }
.dsh-empty { display: flex; min-height: 45vh; align-items: center; justify-content: center; gap: 10px; color: var(--muted-foreground); font-size: 13px; }
.dsh-welcome { margin-bottom: 24px; text-align: center; }
.dsh-welcome h2 { margin: 0; color: var(--foreground); font-size: 28px; font-weight: 600; line-height: 1.35; text-wrap: balance; }
.dsh-config-warning { margin: 12px 0 0; color: color-mix(in srgb, #d97706 70%, var(--foreground)); font-size: 13px; line-height: 1.6; }
.dsh-welcome-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 12px; margin-top: 20px; }
.dsh-welcome-actions button { display: inline-flex; min-height: 36px; align-items: center; justify-content: center; gap: 8px; padding: 8px 10px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--muted-foreground); font: inherit; font-size: 13px; line-height: 20px; cursor: pointer; transition: background-color 160ms ease-out, color 160ms ease-out; }
.dsh-welcome-actions button svg { width: 16px; height: 16px; flex: none; }
.dsh-welcome-actions button:hover:not(:disabled) { background: var(--studio-hover); color: var(--foreground); }
.dsh-welcome-actions button:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.dsh-welcome-actions button:disabled { opacity: .5; cursor: not-allowed; }
.dsh-message { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; margin: 0 auto 32px; max-width: 760px; }
.dsh-avatar { display: grid; width: 29px; height: 29px; place-items: center; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--muted-foreground); }
.dsh-message.assistant .dsh-avatar { border-color: var(--primary); background: var(--primary); color: var(--primary-foreground); }
.dsh-message-content { min-width: 0; }
.dsh-message-media { margin-top: 12px; }
.dsh-message.user { grid-template-columns: minmax(0, 1fr) 30px; }
.dsh-message.user .dsh-avatar { grid-column: 2; grid-row: 1; }
.dsh-message.user .dsh-message-content { grid-column: 1; grid-row: 1; justify-self: end; max-width: min(85%, 600px); }
.dsh-message.user .dsh-message-meta { justify-content: flex-end; }
.dsh-message-meta { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 9px; margin: 2px 0 7px; }
.dsh-message-meta strong { color: var(--foreground); font-size: 12px; }
.dsh-message-meta time { color: var(--muted-foreground); font-size: 10px; }
.dsh-message-expert { color: var(--muted-foreground); font-size: 12px; }
.dsh-message-text { margin: 0; color: var(--foreground); font-size: 15px; line-height: 1.8; overflow-wrap: anywhere; }
.dsh-message.user .dsh-message-text { border-radius: 14px 3px 14px 14px; background: var(--secondary); padding: 10px 14px; white-space: pre-wrap; }
.dsh-message-usage { margin-top: 8px; color: var(--muted-foreground); font-size: 10px; }
.dsh-message-error { margin-top: 6px; color: color-mix(in srgb, var(--destructive) 65%, var(--foreground)); font-size: 11px; }
.dsh-composer-wrap { width: min(100%, 872px); margin: 0 auto; padding: 10px 28px 17px; }
.is-welcome .dsh-composer-wrap { grid-row: 2; width: min(100%, 776px); align-self: center; padding-block: 24px 88px; }
.dsh-background-run { margin: 7px 3px 0; color: var(--muted-foreground); font-size: 11px; }
.dsh-history-button { display: grid; width: 0; height: 32px; flex: none; place-items: center; overflow: hidden; margin-right: -12px; padding: 0; border: 0; border-radius: 6px; opacity: 0; visibility: hidden; background: transparent; color: var(--muted-foreground); cursor: pointer; transition: width var(--dsh-sidebar-duration) var(--dsh-sidebar-ease), margin-right var(--dsh-sidebar-duration) var(--dsh-sidebar-ease), opacity var(--dsh-sidebar-duration) ease-out, visibility 0s linear var(--dsh-sidebar-duration); }
.is-sidebar-collapsed .dsh-history-button { width: 32px; margin-right: 0; opacity: 1; visibility: visible; transition-delay: 0s; }
.dsh-history-button:hover { background: var(--studio-hover); color: var(--foreground); }
.dsh-history-button:focus-visible { outline: 2px solid var(--ring); outline-offset: 1px; }
@media (max-width: 760px) {
  .dsh-page { grid-template-columns: 0px minmax(0, 1fr); }
  .dsh-mobile-scrim { position: fixed; z-index: 40; inset: var(--studio-nav-height) 0 0; border: 0; background: rgb(0 0 0 / 0.4); }
  .dsh-history-button { width: 32px; margin-right: 0; opacity: 1; visibility: visible; transition: none; }
  .dsh-chat-header { justify-content: flex-start; padding: 0 12px; }
  .dsh-messages { padding: 20px 14px 28px; }
  .dsh-composer-wrap { padding: 8px 12px max(12px, env(safe-area-inset-bottom)); }
  .is-welcome .dsh-composer-wrap { padding: 24px 12px 48px; }
  .dsh-welcome h2 { font-size: 24px; }
  .dsh-welcome-actions { gap: 6px; margin-top: 16px; }
  .dsh-runtime-badge { display: none; }
  .dsh-chat-header > div { flex: 1; flex-wrap: wrap; gap: 2px 10px; }
}
@media (pointer: coarse) { .dsh-history-button { height: 44px; } .is-sidebar-collapsed .dsh-history-button { width: 44px; } .dsh-welcome-actions button { min-height: 44px; } }
@media (pointer: coarse) { .dsh-latest-button { width: 44px; height: 44px; } }
@media (max-width: 380px) { .dsh-welcome-actions button { gap: 6px; padding-inline: 6px; font-size: 12px; } }
@media (pointer: coarse) and (max-width: 760px) { .dsh-history-button { width: 44px; } }
@media (prefers-reduced-motion: reduce) { .dsh-page { --dsh-sidebar-duration: 0ms; } .animate-spin { animation: none; } .dsh-latest-button, .dsh-welcome-actions button { transition: none; } .dsh-latest-button:hover, .dsh-latest-button:active { transform: translateX(-50%); } }
@media (prefers-reduced-transparency: reduce) { .dsh-latest-button { background: var(--studio-panel); -webkit-backdrop-filter: none; backdrop-filter: none; } }
@container (max-width: 700px) { .dsh-messages { padding-inline: 16px; } }
</style>
