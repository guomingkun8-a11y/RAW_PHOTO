import { computed, reactive } from "vue";
import { webConfig } from "@/lib/config";
import { sessionState } from "@/stores/session";
import { feedbackApi } from "../feedback-api";
import type { FeedbackAttachment, FeedbackMessage, FeedbackThread, MessageDraft, ThreadDraft } from "../types";

export const feedbackDrafts = new Map<string, { content: string; attachments: FeedbackAttachment[]; internal: boolean; requestId: string; signature: string }>();

export const feedback = reactive({
  open: false, expanded: false, view: "list" as "list" | "chat" | "new",
  connected: false, connecting: false, unread: 0, threads: [] as FeedbackThread[], total: 0,
  page: 1, search: "", status: "", category: "", unreadOnly: false,
  selected: null as FeedbackThread | null, messages: [] as FeedbackMessage[], hasOlder: false,
  loading: false, loadingMessages: false, loadingOlder: false, error: "", active: false,
});
export const isFeedbackAdmin = computed(() => sessionState.session?.role === "admin");

let socket: WebSocket | null = null;
let heartbeat = 0;
let reconnect = 0;
let refreshTimer = 0;
let eventTimer = 0;
let attempts = 0;
let generation = 0;
let selectedGeneration = 0;
let loadedThreadId = "";
let listGeneration = 0;
let refreshPending: Promise<void> | null = null;
let listPending: Promise<void> | null = null;
let resyncAgain = false;
const pending = new Map<string, { resolve: (message: FeedbackMessage) => void; reject: (error: Error) => void; timer: number }>();
const seenEvents = new Set<string>();
const reading = new Set<string>();

export async function loadFeedbackThreads() {
  const version = ++listGeneration;
  feedback.loading = true;
  const params = new URLSearchParams({ page: String(feedback.page), limit: "25" });
  if (feedback.search.trim()) params.set("search", feedback.search.trim());
  if (feedback.status) params.set("status", feedback.status);
  if (feedback.category) params.set("category", feedback.category);
  if (feedback.unreadOnly) params.set("unread_only", "true");
  try {
    const result = await feedbackApi.threads(params);
    if (version !== listGeneration) return;
    feedback.threads = result.items;
    feedback.total = result.total;
    feedback.error = "";
  } catch (error) {
    if (version === listGeneration) feedback.error = error instanceof Error ? error.message : "反馈加载失败";
  } finally {
    if (version === listGeneration) feedback.loading = false;
  }
}

export async function refreshFeedbackUnread() {
  const version = generation;
  const result = await feedbackApi.unread();
  if (version === generation) feedback.unread = result.unread;
}

function mergeMessages(items: FeedbackMessage[]) {
  const messages = new Map(feedback.messages.map((message) => [message.id, message]));
  items.forEach((message) => messages.set(message.id, message));
  feedback.messages = [...messages.values()].sort((a, b) => a.seq - b.seq);
}

export async function selectFeedbackThread(thread: FeedbackThread | string) {
  const id = typeof thread === "string" ? thread : thread.id;
  feedback.view = "chat";
  if (feedback.selected?.id === id && (feedback.loadingMessages || loadedThreadId === id)) return;
  const version = ++selectedGeneration;
  loadedThreadId = "";
  feedback.loadingMessages = true;
  feedback.messages = [];
  feedback.selected = typeof thread === "string" ? null : thread;
  try {
    const result = await feedbackApi.messages(id);
    if (version !== selectedGeneration) return;
    feedback.selected = result.thread;
    feedback.messages = result.items;
    feedback.hasOlder = result.has_more;
    loadedThreadId = id;
    feedback.error = "";
  } catch (error) {
    if (version === selectedGeneration) feedback.error = error instanceof Error ? error.message : "消息加载失败";
  } finally {
    if (version === selectedGeneration) feedback.loadingMessages = false;
  }
}

export async function loadOlderFeedback() {
  if (!feedback.selected || feedback.loadingOlder || !feedback.messages.length) return;
  const id = feedback.selected.id;
  const version = selectedGeneration;
  feedback.loadingOlder = true;
  try {
    const result = await feedbackApi.messages(id, `before=${feedback.messages[0]!.seq}`);
    if (version !== selectedGeneration) return;
    mergeMessages(result.items);
    feedback.hasOlder = result.has_more;
  } finally {
    feedback.loadingOlder = false;
  }
}

export async function markFeedbackRead(seq?: number) {
  const version = generation;
  const thread = feedback.selected;
  const last = seq ?? feedback.messages.at(-1)?.seq ?? 0;
  if (!thread || reading.has(thread.id) || !feedback.active || document.hidden || last <= thread.read_seq) return;
  reading.add(thread.id);
  try {
    const result = await feedbackApi.read(thread.id, last);
    if (version !== generation) return;
    if (feedback.selected?.id === thread.id) {
      feedback.selected.read_seq = result.read_seq;
      feedback.selected.unread = 0;
    }
    const item = feedback.threads.find((item) => item.id === thread.id);
    if (item) item.unread = 0;
    await refreshFeedbackUnread();
  } finally {
    reading.delete(thread.id);
  }
}

async function syncSelected() {
  if (!feedback.selected || !feedback.active || feedback.loadingMessages) return;
  const id = feedback.selected.id;
  const version = selectedGeneration;
  let after = feedback.messages.at(-1)?.seq ?? 0;
  if (!after) {
    const result = await feedbackApi.messages(id);
    if (version === selectedGeneration) {
      feedback.selected = result.thread;
      mergeMessages(result.items);
      feedback.hasOlder = result.has_more;
    }
    return;
  }
  while (true) {
    const result = await feedbackApi.messages(id, `after=${after}`);
    if (version !== selectedGeneration) return;
    mergeMessages(result.items);
    feedback.selected = result.thread;
    if (!result.has_more || !result.items.length) break;
    after = result.items.at(-1)!.seq;
  }
}

export function syncFeedback() {
  if (refreshPending) {
    resyncAgain = true;
    return refreshPending;
  }
  const version = generation;
  const run = (async () => {
    do {
      resyncAgain = false;
      await Promise.all([refreshFeedbackUnread(), syncSelected()]);
      if (version !== generation) return;
      if (feedback.open || feedback.active) {
        if (!listPending) listPending = loadFeedbackThreads().finally(() => { listPending = null; });
        await listPending;
      }
    } while (resyncAgain);
  })().catch((error) => {
    if (version === generation && feedback.active) feedback.error = error instanceof Error ? error.message : "消息同步失败";
  }).finally(() => { if (refreshPending === run) refreshPending = null; });
  refreshPending = run;
  return run;
}

export async function createFeedback(draft: ThreadDraft) {
  const version = generation;
  const thread = await feedbackApi.create(draft);
  if (version !== generation) return;
  await selectFeedbackThread(thread);
  await syncFeedback();
}

function sendSocket(id: string, draft: MessageDraft): Promise<FeedbackMessage> {
  return new Promise((resolve, reject) => {
    const requestId = typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `client-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const timer = window.setTimeout(() => {
      pending.delete(requestId);
      reject(new Error("消息确认超时"));
    }, 12000);
    pending.set(requestId, { resolve, reject, timer });
    try {
      socket!.send(JSON.stringify({ type: "send", request_id: requestId, thread_id: id, message: draft }));
    } catch (error) {
      window.clearTimeout(timer);
      pending.delete(requestId);
      reject(error);
    }
  });
}

export async function sendFeedbackMessage(draft: MessageDraft) {
  const version = generation;
  if (!feedback.selected) throw new Error("请先选择反馈");
  const id = feedback.selected.id;
  // HTTP 负责持久化提交，WebSocket 只负责实时同步。
  const message = await feedbackApi.send(id, draft);
  if (version !== generation) return;
  if (feedback.selected?.id === id) mergeMessages([message]);
  void syncFeedback();
}

export async function updateFeedback(changes: object) {
  if (!feedback.selected) return;
  const thread = await feedbackApi.update(feedback.selected.id, changes);
  if (feedback.selected?.id === thread.id) feedback.selected = thread;
  await syncFeedback();
}

function scheduleSync() {
  if (document.hidden) return;
  window.clearTimeout(eventTimer);
  eventTimer = window.setTimeout(() => { void syncFeedback(); }, 180);
}

function connect(token: string, version: number) {
  if (version !== generation) return;
  feedback.connecting = true;
  const url = new URL(`${webConfig.apiUrl}/api/feedback/ws`, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const connection = new WebSocket(url);
  socket = connection;
  connection.onopen = () => connection.send(JSON.stringify({ type: "auth", token }));
  connection.onmessage = (event) => {
    if (version !== generation) return;
    let frame;
    try { frame = JSON.parse(event.data); } catch { return; }
    if (frame.type === "ready") {
      feedback.connected = true;
      feedback.connecting = false;
      attempts = 0;
      heartbeat = window.setInterval(() => {
        if (connection.readyState === WebSocket.OPEN) connection.send(JSON.stringify({ type: "ping" }));
      }, 25000);
      void syncFeedback();
    } else if (frame.type === "ping") connection.send(JSON.stringify({ type: "pong" }));
    else if (frame.type === "ack" || frame.type === "error") {
      const item = pending.get(frame.request_id);
      if (!item) return;
      pending.delete(frame.request_id);
      window.clearTimeout(item.timer);
      if (frame.type === "ack") item.resolve(frame.message);
      else item.reject(Object.assign(new Error(typeof frame.message === "string" ? frame.message : "消息发送失败"), { status: frame.status }));
    } else if (frame.type === "event" && !seenEvents.has(frame.event_id)) {
      seenEvents.add(frame.event_id);
      if (seenEvents.size > 500) seenEvents.delete(seenEvents.values().next().value!);
      scheduleSync();
    }
  };
  connection.onclose = (event) => {
    if (version !== generation) return;
    feedback.connected = false;
    feedback.connecting = false;
    window.clearInterval(heartbeat);
    for (const item of pending.values()) {
      window.clearTimeout(item.timer);
      item.reject(new Error("连接已断开"));
    }
    pending.clear();
    if (event.code === 4401) return;
    reconnect = window.setTimeout(() => connect(token, version), Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) + Math.random() * 500);
  };
}

function visibilityChanged() {
  if (!document.hidden) void syncFeedback();
}

export function startFeedback(token: string) {
  stopFeedback();
  const version = generation;
  connect(token, version);
  refreshTimer = window.setInterval(() => { if (!document.hidden) void syncFeedback(); }, 30000);
  document.addEventListener("visibilitychange", visibilityChanged);
  void refreshFeedbackUnread().catch(() => {});
}

export function stopFeedback() {
  generation++;
  selectedGeneration++;
  loadedThreadId = "";
  listGeneration++;
  window.clearInterval(heartbeat);
  window.clearInterval(refreshTimer);
  window.clearTimeout(reconnect);
  window.clearTimeout(eventTimer);
  socket?.close();
  socket = null;
  for (const item of pending.values()) {
    window.clearTimeout(item.timer);
    item.reject(new Error("会话已结束"));
  }
  pending.clear();
  seenEvents.clear();
  feedbackDrafts.clear();
  reading.clear();
  feedback.connected = false;
  feedback.open = feedback.active = false;
  feedback.unread = 0;
  feedback.selected = null;
  feedback.messages = [];
  feedback.threads = [];
  feedback.total = 0;
  feedback.page = 1;
  feedback.search = feedback.status = feedback.category = feedback.error = "";
  feedback.view = "list";
  feedback.unreadOnly = feedback.loading = feedback.loadingMessages = feedback.loadingOlder = false;
  refreshPending = listPending = null;
  document.removeEventListener("visibilitychange", visibilityChanged);
}
