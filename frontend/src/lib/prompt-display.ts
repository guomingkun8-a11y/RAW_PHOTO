const USER_REQUEST_LABELS = ["用户原话", "用户需求", "用户请求", "用户提示词"];

const GENERATED_SECTION_MARKERS = [
  "营销文案与版式策略：",
  "本轮最新指令硬约束：",
  "参考图使用规则：",
  "批量单图执行：",
  "视觉导演策略：",
  "标准 Prompt 约束：",
  "通用视觉约束：",
  "高级文字排版约束：",
  "文字语言约束：",
  "画面结构约束：",
  "合规约束：",
  "通用合规约束：",
  "负面约束：",
  "参考图约束：",
  "主体保真：",
  "Product subject preservation mode.",
  "Overlay typography may be added outside the product area.",
];

function normalizePromptText(value: unknown) {
  return String(value || "").replace(/\r\n/g, "\n").trim();
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function firstPositiveMarkerIndex(text: string) {
  const positions = GENERATED_SECTION_MARKERS
    .map((marker) => text.indexOf(marker))
    .filter((position) => position > 0);
  return positions.length ? Math.min(...positions) : -1;
}

function firstBoundaryIndex(text: string) {
  const positions = [
    text.search(/\n\s*\n/),
    ...GENERATED_SECTION_MARKERS.map((marker) => text.indexOf(`\n${marker}`)),
  ].filter((position) => position >= 0);
  return positions.length ? Math.min(...positions) : -1;
}

function stripGeneratedSections(value: string) {
  const text = normalizePromptText(value);
  const firstMarker = firstPositiveMarkerIndex(text);
  return (firstMarker >= 0 ? text.slice(0, firstMarker) : text).trim();
}

function extractLabeledUserRequest(text: string) {
  for (const label of USER_REQUEST_LABELS) {
    const matcher = new RegExp(`${escapeRegExp(label)}\\s*[：:]\\s*`, "g");
    const matches = Array.from(text.matchAll(matcher));
    if (!matches.length) continue;
    const last = matches[matches.length - 1];
    const start = (last.index || 0) + last[0].length;
    const rest = text.slice(start);
    const boundary = firstBoundaryIndex(rest);
    const request = stripGeneratedSections(boundary >= 0 ? rest.slice(0, boundary) : rest);
    if (request) return request;
  }
  return "";
}

function extractEnglishUserRequest(text: string) {
  const matches = Array.from(text.matchAll(/User request\s*[：:]\s*/gi));
  if (!matches.length) return "";
  const last = matches[matches.length - 1];
  const start = (last.index || 0) + last[0].length;
  return stripGeneratedSections(text.slice(start));
}

function startsWithGeneratedSection(text: string) {
  const trimmed = text.trim();
  return GENERATED_SECTION_MARKERS.some((marker) => trimmed.startsWith(marker))
    || /^User request\s*[：:]\s*$/i.test(trimmed);
}

function fallbackVisibleBlock(text: string) {
  const blocks = text.split(/\n\s*\n+/).map((block) => block.trim()).filter(Boolean);
  const visibleBlocks = blocks.filter((block) => !startsWithGeneratedSection(block));
  return stripGeneratedSections(visibleBlocks.at(-1) || "");
}

export function extractUserDisplayPrompt(value: unknown) {
  const text = normalizePromptText(value);
  if (!text) return "";
  const stripped = stripGeneratedSections(text);
  return extractLabeledUserRequest(text)
    || extractEnglishUserRequest(text)
    || (stripped && !startsWithGeneratedSection(stripped) ? stripped : "")
    || fallbackVisibleBlock(text)
    || stripped
    || text;
}

export function imageLibraryDisplayPrompt(
  item: { prompt?: string | null; revised_prompt?: string | null } | null | undefined,
  fallback = "未记录 Prompt",
) {
  return extractUserDisplayPrompt(item?.prompt) || extractUserDisplayPrompt(item?.revised_prompt) || fallback;
}
