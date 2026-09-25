import {
  createImageEditTask,
  createImageGenerationTask,
  createVideoGenerationTask,
  downloadImageTaskZip,
  fetchAgentVideoStatuses,
  fetchSettingsConfig,
  preuploadImageReferences,
  resolveApiAssetUrl,
  retryAgentVideoAnalysis,
  uploadAgentVideos,
  type ImageTask,
  type VideoGenerationDuration,
  type VideoGenerationTask,
} from "@/lib/api";
import { httpRequest } from "@/lib/request";
import { BUILTIN_IMAGE_MODELS } from '@/lib/image-models';
import { normalizeVideoModelSpecs } from "@/lib/video-models";
import {
  waitForCanvasImageTask,
  waitForCanvasVideoTask,
} from '@/features/infinite-canvas/services/canvas-task-poller';
import type {
  CanvasModelOptions,
  Capabilities,
  ChatMessage,
  StoryboardScene,
  WorkflowDocument,
  WorkflowSummary,
} from "@/features/infinite-canvas/types";

const TERMINAL_TASK_STATUSES = new Set(["success", "error", "canceled"]);

function taskError(task: ImageTask | VideoGenerationTask) {
  if (task.status === "canceled") return "任务已取消";
  return task.error || "生成任务失败";
}

function imageItemUrl(item: NonNullable<ImageTask["data"]>[number]) {
  if (item.url) return resolveApiAssetUrl(item.url);
  return item.b64_json ? `data:image/png;base64,${item.b64_json}` : "";
}

function imageResultUrls(task: ImageTask) {
  return (task.data || []).map(imageItemUrl).filter(Boolean);
}

function videoResultUrl(task: VideoGenerationTask) {
  const result = task.video_url || task.data?.find((item) => item.url)?.url || "";
  return resolveApiAssetUrl(result);
}

export async function loadCanvasRuntime(): Promise<{
  capabilities: Capabilities;
  models: CanvasModelOptions;
}> {
  const [settingsResult] = await Promise.allSettled([fetchSettingsConfig()]);
  const settings = settingsResult.status === "fulfilled" ? settingsResult.value.config : null;
  const imageModels = [...BUILTIN_IMAGE_MODELS];
  const videoSpecs = normalizeVideoModelSpecs(settings?.video_generation?.models);
  const textVideoSpec = videoSpecs.find((item) => item.modes.includes("text_to_video"));
  const imageVideoSpec = videoSpecs.find((item) => item.modes.includes("image_to_video"));
  const videoSettings = settings?.video_generation;
  const relaySettings = settings?.openai_relay;
  const chatConfigured = Boolean(
    relaySettings?.enabled
    && String(relaySettings.base_url || '').trim()
    && (relaySettings.has_api_key || Number(relaySettings.api_key_count || 0) > 0),
  );
  const videoConfigured = Boolean(
    videoSettings?.enabled
    && String(videoSettings.base_url || "").trim()
    && (videoSettings.has_api_key || Number(videoSettings.api_key_count || 0) > 0),
  );

  return {
    capabilities: {
      chat: chatConfigured,
      image: imageModels.length > 0,
      video: videoConfigured,
      referenceImageUpload: Boolean(settings?.image_reference_upload?.enabled),
      videoUpload: Boolean(settings?.video_upload?.enabled),
    },
    models: {
      imageModel: "gpt-image-2",
      imageModels,
      videoModels: videoSpecs,
      textVideoModel: textVideoSpec?.id || "",
      imageVideoModel: imageVideoSpec?.id || "",
      textVideoQuality: textVideoSpec?.default_option || "standard",
      imageVideoQuality: imageVideoSpec?.default_option || "standard",
    },
  };
}

export async function uploadCanvasImage(file: File) {
  const response = await preuploadImageReferences([file]);
  const uploaded = response.items[0];
  if (!uploaded?.url) throw new Error("图片上传结果不完整");
  return uploaded;
}

export async function uploadCanvasVideo(file: File, workflowId: string) {
  const response = await uploadAgentVideos([file], workflowId, true);
  const uploaded = response.items[0];
  if (!uploaded?.url || !uploaded.videoId) throw new Error("视频上传结果不完整");
  const queueError = response.analysisQueueErrors?.find((item) => item.videoId === uploaded.videoId)?.error;
  return {
    ...uploaded,
    analysisStatus: queueError ? 'failed' : uploaded.analysisStatus,
    analysisError: queueError || uploaded.analysisError,
  };
}

export function fetchCanvasVideoAnalysisStatuses(ids: string[]) {
  return fetchAgentVideoStatuses(ids);
}

export async function retryCanvasVideoAnalysis(videoId: string) {
  const response = await retryAgentVideoAnalysis(videoId);
  return response.item;
}

export async function generateCanvasImage(input: {
  taskId: string;
  prompt: string;
  model: string;
  aspectRatio: string;
  imageSize: string;
  quality: string;
  thinkingLevel: string;
  imageCount: number;
  references: string[];
  workflowId: string;
  nodeId: string;
  signal?: AbortSignal;
  onProgress?: (task: ImageTask) => void;
}) {
  const task = input.references.length
    ? await createImageEditTask(
      input.taskId,
      [],
      input.prompt,
      input.model,
      "auto",
      input.quality,
      input.references,
      false,
      input.workflowId,
      input.nodeId,
      undefined,
      undefined,
      undefined,
      0,
      1,
      0,
      0,
        "standard",
      {
        aspectRatio: input.aspectRatio,
        imageSize: input.imageSize,
        thinkingLevel: input.thinkingLevel,
        imageCount: input.imageCount,
        source: "canvas",
        canvasUnits: input.imageCount,
      },
    )
    : await createImageGenerationTask(
      input.taskId,
      input.prompt,
      input.model,
      "auto",
      input.quality,
      input.workflowId,
      input.nodeId,
      undefined,
      undefined,
      undefined,
      0,
      1,
        "standard",
      {
        aspectRatio: input.aspectRatio,
        imageSize: input.imageSize,
        thinkingLevel: input.thinkingLevel,
        imageCount: input.imageCount,
        source: "canvas",
        canvasUnits: input.imageCount,
      },
    );
  input.onProgress?.(task);
  const result = TERMINAL_TASK_STATUSES.has(task.status)
    ? task
    : await waitForCanvasImageTask(task.id || input.taskId, {
      signal: input.signal,
      onProgress: input.onProgress,
    });
  if (result.status !== "success") throw new Error(taskError(result));
  const resultUrls = imageResultUrls(result);
  const resultUrl = resultUrls[0] || "";
  if (!resultUrl) throw new Error("图片任务完成，但没有返回图片地址");
  return { resultUrl, resultUrls, taskId: result.id, cost: result.cost };
}

export async function generateCanvasVideo(input: {
  taskId: string;
  prompt: string;
  model: string;
  mode: "text_to_video" | "image_to_video";
  aspectRatio: string;
  duration: VideoGenerationDuration;
  quality: string;
  references: string[];
  workflowId: string;
  nodeId: string;
  signal?: AbortSignal;
  onProgress?: (task: VideoGenerationTask) => void;
}) {
  const task = await createVideoGenerationTask({
    clientTaskId: input.taskId,
    prompt: input.prompt,
    model: input.model,
    mode: input.mode,
    aspectRatio: input.aspectRatio,
    durationSecs: input.duration,
    quality: input.quality,
    resolution: input.quality,
    imageUrls: input.references,
    conversationId: input.workflowId,
    turnId: input.nodeId,
    source: "canvas",
    canvasUnits: 1,
  });
  input.onProgress?.(task);
  const result = TERMINAL_TASK_STATUSES.has(task.status)
    ? task
    : await waitForCanvasVideoTask(task.id || input.taskId, {
      signal: input.signal,
      onProgress: input.onProgress,
    });
  if (result.status !== "success") throw new Error(taskError(result));
  const resultUrl = videoResultUrl(result);
  if (!resultUrl) throw new Error("视频任务完成，但没有返回视频地址");
  return { resultUrl, resultUrls: [resultUrl], taskId: result.id, cost: result.cost };
}

export async function resumeCanvasGeneration(input: {
  kind: 'image' | 'video';
  taskId: string;
  signal?: AbortSignal;
  onProgress?: (task: ImageTask | VideoGenerationTask) => void;
}) {
  const result: ImageTask | VideoGenerationTask = input.kind === 'image'
    ? await waitForCanvasImageTask(input.taskId, {
      signal: input.signal,
      onProgress: (task) => input.onProgress?.(task),
    })
    : await waitForCanvasVideoTask(input.taskId, {
      signal: input.signal,
      onProgress: (task) => input.onProgress?.(task),
    });
  if (result.status !== 'success') throw new Error(taskError(result));
  const resultUrls = input.kind === 'image'
    ? imageResultUrls(result as ImageTask)
    : [videoResultUrl(result as VideoGenerationTask)].filter(Boolean);
  const resultUrl = resultUrls[0] || "";
  if (!resultUrl) throw new Error('任务已完成，但结果文件不可用');
  return { resultUrl, resultUrls, taskId: result.id, cost: result.cost };
}

async function downloadCanvasImageIndexes(taskId: string, indexes: number[], archiveName: string) {
  const blob = await downloadImageTaskZip({
    folderName: "Canvas-Images",
    items: indexes.map((index) => ({
      taskId,
      imageIndex: index,
      filename: `canvas-image-${index + 1}.png`,
    })),
  });
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = archiveName;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}

export async function downloadCanvasImages(taskId: string, imageCount: number) {
  const count = Math.min(4, Math.max(1, Math.round(imageCount || 1)));
  return downloadCanvasImageIndexes(
    taskId,
    Array.from({ length: count }, (_, index) => index),
    "Canvas-Images.zip",
  );
}

export async function downloadCanvasImage(taskId: string, imageIndex: number) {
  const index = Math.min(3, Math.max(0, Math.round(imageIndex || 0)));
  return downloadCanvasImageIndexes(taskId, [index], `Canvas-Image-${index + 1}.zip`);
}

export const canvasApi = {
  listWorkflows(): Promise<{ items: WorkflowSummary[]; total: number }> {
    return httpRequest(`/api/canvas/workflows?_t=${Date.now()}`);
  },

  getWorkflow(id: string): Promise<WorkflowDocument> {
    return httpRequest(`/api/canvas/workflows/${encodeURIComponent(id)}`);
  },

  saveWorkflow(workflow: WorkflowDocument): Promise<WorkflowDocument> {
    return httpRequest(`/api/canvas/workflows/${encodeURIComponent(workflow.id)}`, {
      method: "PUT",
      body: { workflow },
    });
  },

  deleteWorkflow(id: string): Promise<{ ok: boolean }> {
    return httpRequest(`/api/canvas/workflows/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  deleteWorkflows(ids: string[]): Promise<{ ok: boolean; deletedIds: string[]; deletedCount: number }> {
    return httpRequest('/api/canvas/workflows/batch-delete', {
      method: 'POST',
      body: { workflowIds: ids },
    });
  },

  sendAssistant(input: {
    message: string;
    history: ChatMessage[];
    canvasContext: string;
    workflowId?: string;
    nodeId?: string;
  }): Promise<{ response: string }> {
    return httpRequest("/api/canvas/assistant", { method: "POST", body: input, timeout: 120_000 });
  },

  storyboardScripts(input: { story: string; sceneCount: number; workflowId?: string }): Promise<{
    scripts: StoryboardScene[];
    styleAnchor: string;
  }> {
    return httpRequest("/api/canvas/storyboards/scripts", { method: "POST", body: input, timeout: 120_000 });
  },
};
