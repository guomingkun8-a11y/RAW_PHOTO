import { httpRequest } from "@/lib/request";

import {
  type AudioGenerationModel,
  type AudioGenerationStatus,
  type AudioGenerationTask,
  type AudioGenerationTaskListResponse,
  type AudioGenerationTaskRequest,
  type AudioVoice,
  type AudioVoiceListResponse,
} from "@/features/audio-generation/types/audio-generation";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeVoiceResponse(value: unknown, model: AudioGenerationModel): AudioVoiceListResponse {
  const root = isRecord(value) ? value : null;
  const payload = root && isRecord(root.data) ? root.data : root;
  if (!payload || !Array.isArray(payload.voices)) {
    throw new Error("音色接口返回格式不正确，请重试");
  }

  const voices = payload.voices.flatMap<AudioVoice>((item) => {
    if (!isRecord(item)) return [];
    const voiceId = typeof item.voice_id === "string" ? item.voice_id.trim() : "";
    if (!voiceId) return [];
    const name = typeof item.name === "string" && item.name.trim() ? item.name.trim() : voiceId;
    return [{ ...item, voice_id: voiceId, name } as AudioVoice];
  });
  if (!voices.length) throw new Error("暂无可用音色，请稍后重试");

  const requestedDefault = typeof payload.default_voice_id === "string" ? payload.default_voice_id.trim() : "";
  const defaultVoiceId = voices.some((voice) => voice.voice_id === requestedDefault)
    ? requestedDefault
    : voices[0].voice_id;
  return {
    model,
    voices,
    total: voices.length,
    default_voice_id: defaultVoiceId,
  };
}

export async function fetchAudioVoices(model: AudioGenerationModel) {
  const query = new URLSearchParams({ model });
  const response = await httpRequest<unknown>(`/api/audio-generation/voices?${query.toString()}`);
  return normalizeVoiceResponse(response, model);
}

export function createAudioGenerationTask(body: AudioGenerationTaskRequest) {
  return httpRequest<AudioGenerationTask>("/api/audio-generation/tasks", {
    method: "POST",
    body,
  });
}

export function fetchAudioGenerationTasks(
  ids: string[] = [],
  options: {
    limit?: number;
    cursor?: string;
    status?: AudioGenerationStatus;
    q?: string;
    allOwners?: boolean;
    ownerId?: string;
  } = {},
) {
  const params = new URLSearchParams({ _t: String(Date.now()) });
  const uniqueIds = Array.from(new Set(ids.map((id) => id.trim()).filter(Boolean)));
  if (uniqueIds.length) params.set("ids", uniqueIds.join(","));
  if (options.limit) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.status) params.set("status", options.status);
  if (options.q) params.set("q", options.q);
  if (options.allOwners) params.set("all_owners", "true");
  if (options.ownerId) params.set("owner_id", options.ownerId);
  return httpRequest<AudioGenerationTaskListResponse>(`/api/audio-generation/tasks?${params.toString()}`);
}

export function cancelAudioGenerationTask(taskId: string) {
  return httpRequest<AudioGenerationTask>(`/api/audio-generation/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
}

export function deleteAudioGenerationTask(taskId: string) {
  return httpRequest<{ ok: boolean; deleted: number }>(`/api/audio-generation/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}
