import { httpRequest } from "@/lib/request";
import { webConfig } from "@/lib/config";
import { getStoredAuthKey } from "@/stores/auth";

export type DshExpert = { id: string; label: string; description: string };
export type DshSkill = { id: string; label: string; description: string; source: string };
export type DshConnector = { id: string; label: string; description: string; available: boolean };

export type DshAgentModel = {
  id: string;
  label: string;
  protocol: string;
  context_window?: number;
  max_output_tokens?: number;
  compact_at_tokens?: number;
  limit_source?: "deployment";
};

export type DshContextSnapshot = {
  model: string;
  used_tokens: number;
  context_window: number;
  max_output_tokens: number;
  compact_at_tokens: number;
  limit_source: "deployment";
  estimated: boolean;
  compacting: boolean;
  compacted: boolean;
  compaction_failed: boolean;
};

export function dshAgentModelLabel(model: string, models: DshAgentModel[] = []) {
  const labels: Record<string, string> = {
    "gem-3.5-flash": "GEM 3.5 Flash",
    "gem-3.8-flash": "GEM 3.8 Flash",
    "tt-5.6-sol": "GPT 5.6 Sol",
    "tt-6-sol": "GPT 6 Sol",
    "tt-6.1-sol": "GPT 6.1 Sol",
    "deepseek-v4.1-flash": "DS-V4.1-Flash",
    "deepseek-v4-flash": "DS-V4-Flash",
  };
  return labels[model] || models.find((item) => item.id === model)?.label || model || "AI 智能体";
}

export type DshAgentStatus = {
  experts?: DshExpert[];
  skills?: DshSkill[];
  connectors?: DshConnector[];
  teams_enabled?: boolean;
  teams_max_members?: number;
  files_enabled?: boolean;
  image_tools_enabled?: boolean;
  image_models?: DshImageModel[];
  image_model?: string;
  enabled: boolean;
  configured: boolean;
  available: boolean;
  queue_available: boolean;
  model: string;
  models: DshAgentModel[];
  tools_enabled: boolean;
  plugins_enabled: boolean;
  search_enabled: boolean;
  fetch_enabled: boolean;
};

export type DshAgentActivity = {
  id: string;
  kind: "queue" | "runtime" | "analysis" | "search" | "fetch" | "answer" | "image" | "context" | "team" | "diagram";
  status: "running" | "completed" | "error" | "stopped";
  title: string;
  detail?: string;
  sources?: { title: string; url: string }[];
};

export type DshAgentConversation = {
  conversation_id: string;
  model: string;
  title: string;
  status: string;
  turn_count: number;
  created_at: string;
  updated_at: string;
  owner_id?: string;
  owner_username?: string;
  owner_name?: string;
};

export type DshAgentMessage = {
  expert_id?: string;
  skill_ids?: string[] | null;
  team?: DshTeam | null;
  context?: DshContextSnapshot | null;
  media?: DshImageMedia[];
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant" | "model_switch";
  status: string;
  content: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  usage: Record<string, number>;
  error_code: string;
  created_at: string;
  owner_id?: string;
  owner_username?: string;
  owner_name?: string;
  conversation_title?: string;
  activities?: DshAgentActivity[];
  timings?: Record<string, number>;
  model_switch?: { from_model: string; to_model: string } | null;
  feedback?: "positive" | "negative" | null;
};

export type DshAgentStreamEvent =
  | { type: "team"; team: DshTeam | null }
  | { type: "context"; context: DshContextSnapshot }
  | { type: "media"; media: DshImageMedia }
  | { type: "start"; conversation_id: string; model: string; user_message_id?: string }
  | { type: "delta"; text: string }
  | { type: "activity"; activity: DshAgentActivity }
  | { type: "timing"; timings: Record<string, number> }
  | { type: "complete"; message: DshAgentMessage }
  | { type: "error"; message: string };

export async function fetchDshAgentStatus() {
  return httpRequest<DshAgentStatus>("/api/dsh-agent/status");
}

export async function fetchDshAgentConversations() {
  return httpRequest<{ items: DshAgentConversation[]; total: number }>("/api/dsh-agent/conversations?limit=100");
}

export async function createDshAgentConversation(model?: string) {
  return httpRequest<DshAgentConversation>("/api/dsh-agent/conversations", {
    method: "POST", body: model ? { model } : undefined,
  });
}

export async function updateDshAgentConversationModel(conversationId: string, model: string) {
  return httpRequest<DshAgentConversation & { model_switch: DshAgentMessage | null }>(
    `/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/model`,
    { method: "PATCH", body: { model } },
  );
}

export async function fetchDshAgentConversation(conversationId: string) {
  return httpRequest<{ conversation: DshAgentConversation; items: DshAgentMessage[]; draft_references?: DshImageMedia[]; team?: DshTeam | null }>(
    `/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export async function deleteDshAgentConversation(conversationId: string) {
  return httpRequest<{ ok: boolean }>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export async function streamDshAgentMessage(
  conversationId: string,
  content: string,
  onEvent: (event: DshAgentStreamEvent) => void,
  signal?: AbortSignal,
  model?: string,
  images?: { reference_ids?: string[]; image_options?: DshImageOptions; file_ids?: string[] },
  textOnly = false,
  collaboration?: { mode: "normal" | "teams"; team_action?: DshTeamAction; team_revision?: string },
  expertId = "",
  skillIds?: string[],
) {
  const authKey = await getStoredAuthKey();
  const response = await fetch(
    `${webConfig.apiUrl}/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
    {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        ...(authKey ? { Authorization: `Bearer ${authKey}` } : {}),
      },
      body: JSON.stringify({ content, ...(model ? { model } : {}), ...images, ...(textOnly ? { text_only: true } : {}), ...(collaboration?.mode === "teams" ? collaboration : {}), ...(expertId ? { expert_id: expertId } : {}), ...(skillIds !== undefined ? { skill_ids: skillIds } : {}) }),
      signal,
    },
  );
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json() as { detail?: string | { error?: string } };
      message = typeof payload.detail === "string" ? payload.detail : payload.detail?.error || message;
    } catch {
      // Keep the HTTP status fallback.
    }
    throw new Error(message);
  }
  if (!response.body) throw new Error("当前浏览器不支持读取智能体事件流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      if (done && buffer.trim()) buffer += "\n\n";
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      for (const frame of frames) {
        const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        let event: DshAgentStreamEvent;
        try {
          event = JSON.parse(data) as DshAgentStreamEvent;
        } catch {
          throw new Error("回复事件格式异常");
        }
        if (event.type === "error") throw new Error(event.message);
        onEvent(event);
        if (event.type === "complete") return;
      }
      if (done) throw new Error("连接已断开，未收到回复完成确认");
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function fetchDshAgentAdminUsage(limit = 100) {
  return httpRequest<{
    summary: { assistant_turns: number; input_tokens: number; output_tokens: number; total_tokens: number; cost_available: boolean };
    items: DshAgentMessage[];
  }>(`/api/dsh-agent/admin/usage?limit=${limit}`);
}

export type DshTeamAction = "approve" | "discard" | "resume";
export type DshTeam = {
  id: string; name: string; phase: string; halted: boolean; revision: string; error?: string;
  members: { name: string; role?: string; status: string; model?: string }[];
  tasks: { id: string; subject: string; description?: string; status: string; assignee?: string; dependencies?: string[]; blocked_by?: string[]; output?: string }[];
};

export function fetchDshTeam(conversationId: string) {
  return httpRequest<{ team: DshTeam | null; status?: string }>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/team`);
}

export function stopDshAgentRun(conversationId: string, userMessageId: string) {
  return httpRequest<{ accepted: boolean }>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/stop`, {
    method: "POST", body: { user_message_id: userMessageId },
  });
}

export function rateDshAgentMessage(message: DshAgentMessage, feedback: DshAgentMessage["feedback"]) {
  return httpRequest<DshAgentMessage>(`/api/dsh-agent/conversations/${encodeURIComponent(message.conversation_id)}/messages/${encodeURIComponent(message.message_id)}/feedback`, {
    method: "PATCH", body: { feedback },
  });
}

export function discardDshAgentDraft(message: DshAgentMessage) {
  return httpRequest<{ ok: boolean }>(`/api/dsh-agent/conversations/${encodeURIComponent(message.conversation_id)}/messages/${encodeURIComponent(message.message_id)}/draft`, { method: "DELETE" });
}

export async function fetchDshAgentAdminConversation(conversationId: string) {
  return httpRequest<{ conversation: DshAgentConversation; items: DshAgentMessage[] }>(
    `/api/dsh-agent/admin/conversations/${encodeURIComponent(conversationId)}`,
  );
}

export type DshImageModel = { id: string; label: string; ratios: string[]; qualities: string[]; resolutions: string[] };
export type DshImageOptions = { model: string };
export type DshImageMedia = {
  media_id: string; conversation_id: string; message_id: string; kind: "reference" | "task" | "file" | "diagram"; task_id: string;
  title?: string; svg?: string;
  size_bytes?: number; format?: string; text_chars?: number; truncated?: boolean;
  url?: string; filename?: string; width?: number; height?: number; model?: string; prompt?: string;
  task?: { id: string; status: string; status_label?: string; error?: string; image_count?: number; size?: string; data?: { url: string; storage_rel?: string }[] };
};

export function uploadDshImage(conversationId: string, file: File) {
  const body = new FormData();
  body.append("file", file);
  return httpRequest<DshImageMedia>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/images`, { method: "POST", body, timeout: 60000 });
}
export function discardDshImage(conversationId: string, id: string) {
  return httpRequest(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/images/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export function queryDshImages(conversationId: string, ids: string[]) {
  return httpRequest<{ items: DshImageMedia[] }>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/images/query`, { method: "POST", body: { ids }, timeout: 20000 });
}

export function uploadDshFile(conversationId: string, file: File) {
  const body = new FormData(); body.append("file", file);
  return httpRequest<DshImageMedia>(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/files`, { method: "POST", body, timeout: 60000 });
}
export function discardDshFile(conversationId: string, id: string) {
  return httpRequest(`/api/dsh-agent/conversations/${encodeURIComponent(conversationId)}/files/${encodeURIComponent(id)}`, { method: "DELETE" });
}
