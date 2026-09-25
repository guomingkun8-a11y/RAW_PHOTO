import { httpRequest, request } from '@/lib/request';
import { uploadAgentVideos } from '@/lib/api';

import type {
  CompositionTask,
  TimelineAudioAsset,
  TimelineCapabilities,
  TimelineDocument,
  TimelineTranscription,
  TimelineVoiceoverInput,
} from '@/features/video-timeline/types/timeline';

export async function uploadTimelineAudio(file: File): Promise<TimelineAudioAsset> {
  const form = new FormData();
  form.append('file', file);
  return httpRequest<TimelineAudioAsset>('/api/video-compositions/audio-assets', {
    method: 'POST',
    body: form,
    timeout: 180_000,
  });
}

export async function uploadTimelineVideo(file: File, workflowId: string) {
  const response = await uploadAgentVideos([file], workflowId, false);
  const uploaded = response.items[0];
  if (!uploaded?.url || !uploaded.videoId) throw new Error('视频上传结果不完整');
  return uploaded;
}

export function getTimelineCapabilities() {
  return httpRequest<TimelineCapabilities>('/api/video-compositions/capabilities');
}

export function generateTimelineVoiceover(input: TimelineVoiceoverInput & {
  model?: string;
  workflowId?: string;
  nodeId?: string;
}) {
  return httpRequest<TimelineAudioAsset>('/api/video-compositions/voiceovers', {
    method: 'POST',
    body: {
      text: input.text,
      voice: input.voice,
      speechRate: input.speechRate,
      emotion: input.emotion,
      emotionScale: input.emotionScale,
      workflowId: input.workflowId || '',
      nodeId: input.nodeId || '',
      ...(input.model ? { model: input.model } : {}),
    },
    timeout: 360_000,
  });
}

export function transcribeTimelineAudio(input: {
  storageRel: string;
  language?: string;
  prompt?: string;
  offset?: number;
  workflowId?: string;
  nodeId?: string;
}) {
  return httpRequest<TimelineTranscription>('/api/video-compositions/transcriptions', {
    method: 'POST',
    body: {
      storageRel: input.storageRel,
      language: input.language || 'zh',
      prompt: input.prompt || '',
      offset: input.offset || 0,
      workflowId: input.workflowId || '',
      nodeId: input.nodeId || '',
    },
    timeout: 360_000,
  });
}

export function createCompositionTask(input: {
  clientTaskId: string;
  workflowId: string;
  timeline: TimelineDocument;
  source?: "standard" | "canvas";
  canvasUnits?: number;
}) {
  return httpRequest<CompositionTask>('/api/video-compositions/tasks', {
    method: 'POST',
    body: {
      client_task_id: input.clientTaskId,
      workflow_id: input.workflowId,
      timeline: input.timeline,
      source: input.source || "standard",
      canvas_units: input.source === "canvas" ? Math.max(1, Math.round(input.canvasUnits || 1)) : 1,
    },
    timeout: 120_000,
  });
}

export function getCompositionTask(taskId: string) {
  return httpRequest<CompositionTask>(`/api/video-compositions/tasks/${encodeURIComponent(taskId)}`);
}

export function listCompositionTasks(workflowId: string, limit = 50) {
  const query = new URLSearchParams({ workflow_id: workflowId, limit: String(limit) });
  return httpRequest<{ items: CompositionTask[]; total: number }>(`/api/video-compositions/tasks?${query}`);
}

export function deleteCompositionTask(taskId: string) {
  return httpRequest<{ ok: boolean }>(`/api/video-compositions/tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  });
}

export function cancelCompositionTask(taskId: string) {
  return httpRequest<CompositionTask>(`/api/video-compositions/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
  });
}

export function retryCompositionTask(taskId: string) {
  return httpRequest<CompositionTask>(`/api/video-compositions/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: 'POST',
  });
}

export async function pollCompositionTask(
  taskId: string,
  onUpdate?: (task: CompositionTask) => void,
  signal?: AbortSignal,
) {
  const started = Date.now();
  while (Date.now() - started < 30 * 60 * 1000) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const task = await getCompositionTask(taskId);
    onUpdate?.(task);
    if (['success', 'error', 'canceled'].includes(task.status)) return task;
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, 1500);
      signal?.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
    });
  }
  throw new Error('合成任务仍在后台运行，请稍后查看。');
}

export async function downloadComposition(url: string, filename: string) {
  const response = await request.get<Blob>(url, { responseType: 'blob', timeout: 180_000 });
  const href = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}
