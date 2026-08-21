<script setup lang="ts">
import { Check, CheckCircle2, ChevronDown, CircleAlert, LoaderCircle, Sparkles, XCircle } from "@lucide/vue";
import { computed, ref, watch } from "vue";

import type { AgentEvent, AgentRun } from "@/lib/api";

const props = defineProps<{ run: AgentRun }>();
const expanded = ref(props.run.status !== "completed");

type EventState = "active" | "completed" | "failed" | "canceled";
type RunResult = NonNullable<AgentRun["result"]>;
type TurnIntent = NonNullable<RunResult["turnIntent"]>;

const isActiveRun = computed(() => props.run.status === "pending" || props.run.status === "running" || props.run.status === "waiting_for_images");
const runResult = computed(() => (props.run.result || {}) as RunResult);
const promptPlan = computed(() => (runResult.value.promptPlan || {}) as RunResult["promptPlan"]);
const turnIntent = computed(() => (runResult.value.turnIntent || {}) as TurnIntent);
const promptModel = computed(() => cleanText(promptPlan.value.model));
const promptScene = computed(() => sceneLabel(cleanText(promptPlan.value.sceneName)));
const promptSize = computed(() => cleanText(promptPlan.value.resolvedSize));
const optimizationRoute = computed(() => cleanText(runResult.value.optimizationRoute));

function textForEvent(event: AgentEvent) {
  const payload = event.payload || {};
  if (event.type === "run.canceled") return typeof payload.message === "string" ? payload.message : "创意智能体已中止。";
  if (event.type === "run.failed" || event.type === "tool.failed") return typeof payload.error === "string" ? payload.error : "创意智能体执行失败。";
  if (event.type === "agent.intent.classified") {
    const intent = typeof payload.intent === "string" ? payload.intent : "";
    const source = typeof payload.source === "string" ? payload.source : "";
    const reason = typeof payload.reason === "string" ? payload.reason : "";
    const pieces = [
      intent ? `意图：${intent}` : "",
      source ? `来源：${source}` : "",
      reason,
    ].filter(Boolean);
    return pieces.join(" · ") || "已完成意图判定。";
  }
  if (event.type === "tool.started" || event.type === "tool.completed" || event.type === "tool.failed") {
    const toolName = toolNameFromPayload(payload);
    const suffix = event.type === "tool.started" ? "正在调用" : event.type === "tool.completed" ? "调用完成" : "调用失败";
    return toolName ? `${toolName} · ${suffix}` : suffix;
  }
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return payload.summary
      .trim()
      .replace(/CowAgent is analyzing the request and persistent context\.?/gi, "正在读取本轮需求与长期记忆。")
      .replace(/CowAgent/gi, "专业模式");
  }
  if (event.type === "run.started") return "已开始理解你的生图目标。";
  if (event.type === "run.resumed") return "已收到你的决定，继续执行当前方案。";
  if (event.type === "decision.made") return `下一步：${typeof payload.title === "string" ? payload.title : "继续执行"}`;
  if (event.type === "run.completed") return "本轮智能体任务已完成。";
  if (event.type === "run.waiting_for_images") return "方案已提交，正在后台生成图片。";
  if (event.type === "run.waiting_for_input") return typeof payload.message === "string" ? payload.message : "方案已就绪，等待你确认或调整。";
  return "正在处理。";
}

const timelineEvents = computed(() => {
  const meaningfulTypes = new Set([
    "run.started",
    "run.resumed",
    "decision.made",
    "agent.intent.classified",
    "agent.update",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "run.completed",
    "run.failed",
    "run.canceled",
    "run.waiting_for_images",
    "run.waiting_for_input",
  ]);
  const source = (props.run.events || []).filter((event) => meaningfulTypes.has(event.type));
  const latestPhaseSequence = new Map<string, number>();
  for (const event of source) {
    if (event.type !== "agent.update") continue;
    const phase = String(event.payload?.phase || "").trim();
    if (phase) latestPhaseSequence.set(phase, event.sequence);
  }

  const compacted = source.filter((event) => {
    if (event.type !== "agent.update") return true;
    const phase = String(event.payload?.phase || "").trim();
    return !phase || latestPhaseSequence.get(phase) === event.sequence;
  });
  return compacted
    .filter((event, index, events) => index === 0 || textForEvent(event) !== textForEvent(events[index - 1]))
    .slice(-12);
});

const latestToolEvent = computed(() => [...timelineEvents.value].reverse().find((event) => event.type === "tool.started" || event.type === "tool.completed" || event.type === "tool.failed"));
const latestToolName = computed(() => toolNameFromPayload(latestToolEvent.value?.payload));
const progressMeta = computed(() => [
  promptModel.value ? { label: "模型", value: promptModel.value } : null,
  optimizationRoute.value ? { label: "路由", value: routeLabel(optimizationRoute.value) } : null,
  turnIntent.value?.intent ? { label: "意图", value: buildIntentValue(turnIntent.value) } : null,
  latestToolName.value ? { label: "工具", value: buildToolValue(latestToolEvent.value?.type, latestToolName.value) } : null,
  promptScene.value ? { label: "场景", value: promptScene.value } : null,
  promptSize.value ? { label: "画布", value: promptSize.value } : null,
].filter(Boolean) as Array<{ label: string; value: string }>);

const activeSequence = computed(() => isActiveRun.value ? timelineEvents.value.at(-1)?.sequence : undefined);
const agentTitle = computed(() => {
  if (props.run.status === "pending" || props.run.status === "running") return "专业模式";
  if (props.run.status === "waiting_for_images") return "专业模式";
  if (props.run.status === "completed") return "专业模式";
  if (props.run.status === "failed") return "专业模式";
  if (props.run.status === "canceled") return "专业模式";
  return "等待你的确认";
});
const statusLabel = computed(() => {
  if (props.run.status === "canceled") return "已中止";
  if (props.run.status === "pending") return "准备中";
  if (props.run.status === "running") return "运行中";
  if (props.run.status === "waiting_for_images") return "生成中";
  if (props.run.status === "completed") return "已完成";
  if (props.run.status === "failed") return "失败";
  return "等待输入";
});
const statusClass = computed(() => {
  if (props.run.status === "canceled") return "is-canceled";
  if (props.run.status === "completed") return "is-success";
  if (props.run.status === "failed") return "is-failed";
  return "is-running";
});

const headerIcon = computed(() => {
  if (props.run.status === "completed") return CheckCircle2;
  if (props.run.status === "failed" || props.run.status === "canceled") return XCircle;
  if (props.run.status === "waiting_for_input") return CircleAlert;
  return LoaderCircle;
});

const headerSummary = computed(() => {
  const modelCalls = Number(props.run.result?.modelUsage?.totalCalls || 0);
  const calls = [
    modelCalls ? `${modelCalls} 次模型调用` : "",
    `${props.run.toolCalls || 0} 次工具调用`,
  ].filter(Boolean).join(" · ");
  const duration = formatDuration(props.run.durationMs);
  return duration ? `${calls} · ${duration}` : calls;
});

watch(() => props.run.status, (status, previousStatus) => {
  if (status === "pending" || status === "running" || status === "waiting_for_images") expanded.value = true;
  else if (status === "completed" && previousStatus !== "completed") expanded.value = false;
});

function formatDuration(durationMs?: number) {
  if (!durationMs || durationMs <= 0) return "";
  if (durationMs < 1000) return "不到 1 秒";
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`;
}

function cleanText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function toolNameFromPayload(payload: unknown) {
  if (!payload || typeof payload !== "object") return "";
  const value = (payload as Record<string, unknown>).toolName || (payload as Record<string, unknown>).tool_name;
  return cleanText(value);
}

function buildIntentValue(intent: TurnIntent) {
  const intentName = cleanText(intent.intent);
  const source = cleanText(intent.source);
  const confidence = typeof intent.confidence === "number" ? `${Math.round(intent.confidence * 100)}%` : "";
  return [intentName, source ? `来源 ${source}` : "", confidence].filter(Boolean).join(" · ");
}

function buildToolValue(eventType: string | undefined, toolName: string) {
  const state = eventType === "tool.completed" ? "已完成" : eventType === "tool.failed" ? "失败" : "调用中";
  return `${toolName} · ${state}`;
}

function routeLabel(value: string) {
  const routeMap: Record<string, string> = {
    direct_consult: "对话回答",
    confirmed_generation: "快速生图",
    full_agent: "自主工具链",
    intent_cancel: "已取消",
  };
  return routeMap[value] || value;
}

function sceneLabel(value: string) {
  return value
    .replace(/CowAgent open workflow/gi, "智能体自动规划")
    .replace(/CowAgent/gi, "专业模式");
}

function stateForEvent(event: AgentEvent): EventState {
  if (event.type === "run.canceled") return "canceled";
  if (event.type === "run.failed" || event.type === "tool.failed") return "failed";
  if (activeSequence.value === event.sequence) return "active";
  return "completed";
}

function iconForEvent(event: AgentEvent) {
  const state = stateForEvent(event);
  if (state === "active") return LoaderCircle;
  if (state === "failed" || state === "canceled") return XCircle;
  if (event.type === "run.completed") return CheckCircle2;
  if (event.type === "decision.made") return Sparkles;
  return Check;
}
</script>

<template>
  <section
    class="agent-progress-panel"
    aria-live="polite"
    :aria-busy="isActiveRun"
    :data-state="run.status"
    data-testid="agent-progress-panel"
  >
    <div class="agent-progress-heading">
      <div class="agent-progress-identity">
        <span class="agent-progress-mark" :class="statusClass">
          <component :is="headerIcon" class="agent-progress-header-icon" :class="{ 'is-spinning': isActiveRun }" />
        </span>
        <div class="agent-progress-title-group">
          <div class="agent-progress-title-row">
            <strong>{{ agentTitle }}</strong>
            <span class="agent-progress-status" :class="statusClass">{{ statusLabel }}</span>
          </div>
          <span class="agent-progress-count">{{ headerSummary }}</span>
          <div v-if="progressMeta.length" class="agent-progress-meta">
            <span v-for="item in progressMeta" :key="`${item.label}:${item.value}`" class="agent-progress-meta-item">
              <span class="agent-progress-meta-label">{{ item.label }}</span>
              <span class="agent-progress-meta-value">{{ item.value }}</span>
            </span>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="agent-progress-toggle"
        :aria-expanded="expanded"
        :aria-label="expanded ? '收起执行详情' : '展开执行详情'"
        :title="expanded ? '收起执行详情' : '展开执行详情'"
        @click="expanded = !expanded"
      >
        <ChevronDown class="size-4" :class="{ 'is-expanded': expanded }" />
      </button>
    </div>

    <Transition name="agent-progress-details">
      <div v-show="expanded" class="agent-progress-details">
        <ol class="agent-progress-events" data-testid="agent-progress-events">
          <li
            v-for="event in timelineEvents"
            :key="`${run.runId}-${event.sequence}`"
            class="agent-progress-event"
            :class="[`is-${stateForEvent(event)}`, { 'is-terminal-event': event.type === 'run.completed' }]"
            :data-event-state="stateForEvent(event)"
          >
            <span class="agent-progress-node">
              <component :is="iconForEvent(event)" class="agent-progress-icon" :class="{ 'is-spinning': stateForEvent(event) === 'active' }" />
            </span>
            <span class="agent-progress-event-copy">{{ textForEvent(event) }}</span>
          </li>
        </ol>
        <div v-if="run.status === 'failed' && run.error" class="agent-progress-error">
          <CircleAlert class="size-3.5 shrink-0" />
          <span>{{ run.error }}</span>
        </div>
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.agent-progress-panel {
  margin-top: 0.9rem;
  border-top: 1px solid rgb(15 23 42 / 0.08);
  border-bottom: 1px solid rgb(15 23 42 / 0.08);
  padding: 0.75rem 0.1rem 0.7rem;
}
.agent-progress-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
  color: rgb(15 23 42 / 0.78);
  font-size: 0.75rem;
}
.agent-progress-identity {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.6rem;
}
.agent-progress-mark {
  display: inline-grid;
  place-items: center;
  width: 1.65rem;
  height: 1.65rem;
  flex: none;
  border-radius: 0.45rem;
  background: rgb(79 124 255 / 0.1);
  color: #315be8;
  transition: background-color 180ms var(--studio-ease), color 180ms var(--studio-ease);
}
.agent-progress-mark.is-success { background: rgb(16 185 129 / 0.11); color: rgb(4 120 87); }
.agent-progress-mark.is-failed { background: rgb(244 63 94 / 0.1); color: rgb(190 24 93); }
.agent-progress-mark.is-canceled { background: rgb(100 116 139 / 0.12); color: rgb(71 85 105); }
.agent-progress-header-icon { width: 0.9rem; height: 0.9rem; }
.agent-progress-title-group {
  display: grid;
  min-width: 0;
  gap: 0.16rem;
}
.agent-progress-title-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.45rem;
}
.agent-progress-title-row strong {
  color: rgb(30 41 59);
  font-size: 0.76rem;
  font-weight: 650;
}
.agent-progress-status,
.agent-progress-count {
  color: rgb(100 116 139 / 0.9);
  font-size: 0.68rem;
  font-weight: 500;
}
.agent-progress-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.14rem;
}
.agent-progress-meta-item {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 0.28rem;
  border-radius: 0.42rem;
  background: rgb(15 23 42 / 0.055);
  padding: 0.16rem 0.42rem;
  color: rgb(71 85 105);
  font-size: 0.64rem;
  line-height: 1.2;
}
.agent-progress-meta-label {
  flex: none;
  font-weight: 600;
}
.agent-progress-meta-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-progress-status {
  border-radius: 999px;
  padding: 0.18rem 0.45rem;
  background: rgb(79 124 255 / 0.1);
  color: #315be8;
}
.agent-progress-status.is-success { background: rgb(16 185 129 / 0.1); color: rgb(4 120 87); }
.agent-progress-status.is-failed { background: rgb(244 63 94 / 0.1); color: rgb(190 24 93); }
.agent-progress-status.is-canceled { background: rgb(100 116 139 / 0.12); color: rgb(71 85 105); }
.agent-progress-toggle {
  display: inline-grid;
  width: 1.8rem;
  height: 1.8rem;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 0.45rem;
  background: transparent;
  color: rgb(100 116 139);
  cursor: pointer;
}
.agent-progress-toggle:hover { background: rgb(15 23 42 / 0.055); color: rgb(51 65 85); }
.agent-progress-toggle:focus-visible { outline: 2px solid rgb(79 124 255 / 0.48); outline-offset: 2px; }
.agent-progress-toggle svg { transition: transform 200ms var(--studio-ease); }
.agent-progress-toggle svg.is-expanded { transform: rotate(180deg); }
.agent-progress-details { overflow: hidden; }
.agent-progress-events {
  display: grid;
  gap: 0.16rem;
  margin-top: 0.7rem;
  padding: 0;
  list-style: none;
}
.agent-progress-event {
  position: relative;
  display: grid;
  grid-template-columns: 1.15rem minmax(0, 1fr);
  min-width: 0;
  align-items: flex-start;
  gap: 0.48rem;
  padding: 0.16rem 0;
  color: rgb(71 85 105 / 0.92);
  font-size: 0.72rem;
  line-height: 1.45;
}
.agent-progress-event:not(:last-child)::after {
  position: absolute;
  top: 1.05rem;
  bottom: -0.32rem;
  left: 0.55rem;
  width: 1px;
  background: rgb(148 163 184 / 0.28);
  content: "";
}
.agent-progress-node {
  position: relative;
  z-index: 1;
  display: inline-grid;
  width: 1.15rem;
  height: 1.15rem;
  place-items: center;
  border-radius: 999px;
  background: #f1f5f9;
  color: rgb(100 116 139);
}
.agent-progress-icon { width: 0.68rem; height: 0.68rem; flex: none; }
.agent-progress-event-copy {
  min-width: 0;
  padding: 0.03rem 0 0.2rem;
  overflow-wrap: anywhere;
}
.agent-progress-event.is-active { color: rgb(30 64 175); font-weight: 550; }
.agent-progress-event.is-active .agent-progress-node { background: #315be8; color: #fff; }
.agent-progress-event.is-failed { color: rgb(190 24 93); }
.agent-progress-event.is-failed .agent-progress-node { background: rgb(244 63 94 / 0.12); color: rgb(190 24 93); }
.agent-progress-event.is-canceled .agent-progress-node { background: rgb(100 116 139 / 0.12); color: rgb(71 85 105); }
.agent-progress-event.is-terminal-event { color: rgb(4 120 87); font-weight: 600; }
.agent-progress-event.is-terminal-event .agent-progress-node { background: rgb(16 185 129 / 0.12); color: rgb(4 120 87); }
.agent-progress-icon.is-spinning,
.agent-progress-header-icon.is-spinning {
  animation: agent-progress-spin 0.9s linear infinite;
}
.agent-progress-error { display: flex; gap: 0.45rem; margin-top: 0.65rem; color: rgb(190 24 93); font-size: 0.72rem; line-height: 1.4; }
.agent-progress-details-enter-active { transition: opacity 210ms var(--studio-ease), transform 210ms var(--studio-ease); }
.agent-progress-details-leave-active { transition: opacity 150ms ease, transform 150ms ease; }
.agent-progress-details-enter-from,
.agent-progress-details-leave-to { opacity: 0; transform: translateY(-0.22rem); }
@keyframes agent-progress-spin { to { transform: rotate(360deg); } }
.dark .agent-progress-panel { border-color: rgb(255 255 255 / 0.1); }
.dark .agent-progress-heading, .dark .agent-progress-event { color: rgb(214 211 209 / 0.82); }
.dark .agent-progress-title-row strong { color: rgb(245 245 244 / 0.94); }
.dark .agent-progress-count { color: rgb(168 162 158 / 0.8); }
.dark .agent-progress-meta-item { background: rgb(255 255 255 / 0.06); color: rgb(214 211 209 / 0.86); }
.dark .agent-progress-toggle { color: rgb(168 162 158); }
.dark .agent-progress-toggle:hover { background: rgb(255 255 255 / 0.07); color: rgb(231 229 228); }
.dark .agent-progress-node { background: rgb(255 255 255 / 0.08); color: rgb(168 162 158); }
.dark .agent-progress-event:not(:last-child)::after { background: rgb(255 255 255 / 0.12); }
.dark .agent-progress-event.is-active { color: rgb(165 180 252); }
.dark .agent-progress-event.is-terminal-event { color: rgb(110 231 183); }
@media (prefers-reduced-motion: reduce) {
  .agent-progress-icon.is-spinning,
  .agent-progress-header-icon.is-spinning { animation: none; }
  .agent-progress-toggle svg,
  .agent-progress-details-enter-active,
  .agent-progress-details-leave-active { transition: none; }
}
</style>
