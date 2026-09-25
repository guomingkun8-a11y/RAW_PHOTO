import type { TimelineSubtitleCue } from '@/features/video-timeline/types/timeline';
import { makeTimelineId } from '@/features/video-timeline/composables/useTimeline';

function parseTimestamp(value: string) {
  const match = value.trim().match(/^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$/);
  if (!match) return undefined;
  return Number(match[1]) * 3600
    + Number(match[2]) * 60
    + Number(match[3])
    + Number(match[4].padEnd(3, '0')) / 1000;
}

export function parseSrt(value: string): TimelineSubtitleCue[] {
  const blocks = value.replace(/\r/g, '').trim().split(/\n{2,}/);
  const cues: TimelineSubtitleCue[] = [];
  for (const block of blocks) {
    const lines = block.split('\n');
    const timingIndex = lines.findIndex((line) => line.includes('-->'));
    if (timingIndex < 0) continue;
    const [rawStart, rawEnd] = lines[timingIndex].split('-->').map((item) => item.trim().split(/\s+/)[0]);
    const start = parseTimestamp(rawStart || '');
    const end = parseTimestamp(rawEnd || '');
    const text = lines.slice(timingIndex + 1).join('\n').trim();
    if (start === undefined || end === undefined || end <= start || !text) continue;
    cues.push({ id: makeTimelineId('subtitle'), start, end, text });
  }
  if (!cues.length) throw new Error('没有识别到有效的 SRT 字幕。');
  return cues;
}

export function captionsFromText(text: string, duration: number, offset = 0): TimelineSubtitleCue[] {
  const chunks = text
    .trim()
    .split(/(?<=[。！？!?；;])\s*|\n+/)
    .flatMap((paragraph) => {
      const values: string[] = [];
      let remaining = paragraph.trim();
      while (remaining.length > 24) {
        let splitAt = Math.max(...['，', ',', '、', ' '].map((mark) => remaining.lastIndexOf(mark, 24)));
        if (splitAt < 2) splitAt = 24;
        values.push(remaining.slice(0, splitAt).trim());
        remaining = remaining.slice(splitAt).replace(/^[，,、\s]+/, '');
      }
      if (remaining) values.push(remaining);
      return values;
    })
    .filter(Boolean);
  if (!chunks.length) return [];
  const safeDuration = Math.max(0.5, duration);
  const weights = chunks.map((chunk) => Math.max(1, chunk.length));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  let cursor = 0;
  return chunks.map((textValue, index) => {
    const start = cursor;
    cursor = index === chunks.length - 1 ? safeDuration : cursor + safeDuration * weights[index] / totalWeight;
    return {
      id: makeTimelineId('subtitle'),
      start: offset + start,
      end: offset + Math.max(start + 0.35, cursor),
      text: textValue,
    };
  });
}
