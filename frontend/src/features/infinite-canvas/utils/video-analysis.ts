import type { CanvasVideoAnalysis, VideoAnalysisStatus } from '@/features/infinite-canvas/types';

export const ACTIVE_VIDEO_ANALYSIS_STATUSES = new Set<VideoAnalysisStatus>([
  'pending',
  'queued',
  'processing',
  'analyzing',
]);

function cleanText(value: unknown, limit: number) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function cleanList(value: unknown, limit = 8) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => cleanText(item, 500))
    .filter(Boolean)
    .slice(0, limit);
}

export function normalizeVideoAnalysisStatus(value: unknown): VideoAnalysisStatus {
  const status = String(value || 'pending').trim().toLowerCase();
  if (status === 'ready' || status === 'failed' || status === 'queued'
    || status === 'processing' || status === 'analyzing') return status;
  return 'pending';
}

export function normalizeCanvasVideoAnalysis(value: unknown): CanvasVideoAnalysis | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  const result: CanvasVideoAnalysis = {
    summary: cleanText(source.summary, 2_000),
    sceneSummary: cleanText(source.sceneSummary ?? source.scene_summary, 1_500),
    transcriptSummary: cleanText(source.transcriptSummary ?? source.transcript_summary, 1_500),
    visualDirections: cleanList(source.visualDirections ?? source.visual_directions),
    sellingPoints: cleanList(source.sellingPoints ?? source.selling_points),
    risks: cleanList(source.risks, 10),
  };
  const hasContent = Boolean(
    result.summary
    || result.sceneSummary
    || result.transcriptSummary
    || result.visualDirections?.length
    || result.sellingPoints?.length
    || result.risks?.length,
  );
  return hasContent ? result : undefined;
}

export function videoAnalysisPromptLines(value: CanvasVideoAnalysis | undefined) {
  if (!value) return [];
  const lines: string[] = [];
  if (value.summary) lines.push(`内容摘要：${value.summary}`);
  if (value.sceneSummary) lines.push(`场景与镜头：${value.sceneSummary}`);
  if (value.transcriptSummary) lines.push(`语音摘要：${value.transcriptSummary}`);
  if (value.visualDirections?.length) lines.push(`视觉与运镜：${value.visualDirections.join('；')}`);
  if (value.sellingPoints?.length) lines.push(`可用卖点：${value.sellingPoints.join('；')}`);
  return lines;
}
