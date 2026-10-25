export type ImageDimensions = {
  width?: number;
  height?: number;
};

const IMAGE_SIZE_PRESETS: Record<string, string> = {
  "1:1": "1024x1024",
  "2:3": "1024x1536",
  "3:2": "1536x1024",
  "3:4": "1024x1365",
  "4:3": "1365x1024",
  "4:5": "1024x1280",
  "5:4": "1280x1024",
  "9:16": "1088x1920",
  "16:9": "1920x1088",
};

const COMMON_RATIOS = Object.keys(IMAGE_SIZE_PRESETS).map((label) => {
  const [width, height] = label.split(":").map(Number);
  return { label, value: width / height };
});

export function parseImageSize(size: unknown) {
  const text = typeof size === "string" ? size : "";
  const match = text.match(/^(\d+)x(\d+)$/);
  return match ? { width: match[1], height: match[2] } : { width: "1024", height: "1024" };
}

export function inferImageRatio(size: string, fallback = "auto") {
  const parsed = parseImageSize(size);
  const width = Number(parsed.width);
  const height = Number(parsed.height);
  if (!width || !height) return fallback;
  const ratio = width / height;
  const nearest = COMMON_RATIOS.reduce((best, item) => (
    Math.abs(item.value - ratio) < Math.abs(best.value - ratio) ? item : best
  ));
  return Math.abs(nearest.value - ratio) <= 0.025 ? nearest.label : fallback;
}

function isNegatedSizeMatch(text: string, start: number) {
  const prefix = text.slice(Math.max(0, start - 24), start);
  const boundary = Math.max(...[",", "，", ".", "。", ";", "；", "!", "！", "?", "？"].map((marker) => prefix.lastIndexOf(marker)));
  const clausePrefix = prefix.slice(boundary + 1);
  return /(?:不要|不需要|不用|不是|排除|避免|非)\s*(?:使用|采用|选择|设置(?:为|成)?|做成|生成|输出|比例(?:为|是)?|尺寸(?:为|是)?)?\s*$/.test(clausePrefix);
}

function ratioToImageSize(widthRatio: number, heightRatio: number) {
  const preset = IMAGE_SIZE_PRESETS[`${widthRatio}:${heightRatio}`];
  if (preset) return preset;
  if (widthRatio <= heightRatio) return `1024x${Math.round(1024 * heightRatio / Math.max(1, widthRatio))}`;
  return `${Math.round(1024 * widthRatio / Math.max(1, heightRatio))}x1024`;
}

export function resolveExplicitImageSizeFromPrompt(prompt: string) {
  const text = prompt.toLowerCase().replace(/：/g, ":");
  for (const match of text.matchAll(/(?<!\d)(\d{3,4})\s*[xX×]\s*(\d{3,4})(?!\d)/g)) {
    if (!isNegatedSizeMatch(text, match.index ?? 0)) return `${Number(match[1])}x${Number(match[2])}`;
  }
  for (const match of text.matchAll(/(?<!\d)(\d{1,2})\s*[:比]\s*(\d{1,2})(?!\d)/g)) {
    if (!isNegatedSizeMatch(text, match.index ?? 0)) return ratioToImageSize(Number(match[1]), Number(match[2]));
  }
  if (/(?:横版|横图|横向构图|宽图|landscape)/.test(text)) return IMAGE_SIZE_PRESETS["3:2"];
  if (/(?:竖版|竖图|竖向构图|长图|portrait)/.test(text)) return IMAGE_SIZE_PRESETS["2:3"];
  return null;
}

export function resolveImageSizeFromPrompt(prompt: string, configuredSize: string) {
  const explicit = resolveExplicitImageSizeFromPrompt(prompt);
  if (explicit) return explicit;
  const compact = prompt.toLowerCase().replace(/\s+/g, "");
  const requestsDifferentRatio = [
    "不要1:1", "不需要1:1", "不用1:1", "不是1:1", "非1:1",
    "不要一比一", "不要方图", "不是方图", "非正方形",
    "其他比例", "其它比例", "换个比例", "换一种比例",
  ].some((marker) => compact.includes(marker));
  if (!requestsDifferentRatio) return configuredSize;
  const configured = parseImageSize(configuredSize);
  return configured.width === configured.height ? IMAGE_SIZE_PRESETS["2:3"] : configuredSize;
}

function snapToGrid(value: number) {
  return Math.max(16, Math.round(value / 16) * 16);
}

export function resolveSourceImageSize(source: ImageDimensions | undefined, fallbackSize = "1024x1024") {
  const sourceWidth = Number(source?.width || 0);
  const sourceHeight = Number(source?.height || 0);
  if (!sourceWidth || !sourceHeight) return fallbackSize;
  const fallback = parseImageSize(fallbackSize);
  const shortEdge = Math.max(512, Math.min(2160, Math.min(Number(fallback.width), Number(fallback.height)) || 1024));
  const ratio = Math.max(1 / 3, Math.min(3, sourceWidth / sourceHeight));
  const width = ratio >= 1 ? shortEdge * ratio : shortEdge;
  const height = ratio >= 1 ? shortEdge : shortEdge / ratio;
  return `${snapToGrid(width)}x${snapToGrid(height)}`;
}

export function sourceRatioLabel(source: ImageDimensions | undefined) {
  const width = Number(source?.width || 0);
  const height = Number(source?.height || 0);
  if (!width || !height) return "";
  const ratio = width / height;
  const nearest = COMMON_RATIOS.reduce((best, item) => (
    Math.abs(item.value - ratio) < Math.abs(best.value - ratio) ? item : best
  ));
  return Math.abs(nearest.value - ratio) <= 0.025 ? nearest.label : "自定义";
}
