import type { ImageModel, Model } from "@/lib/api";

export type ImageModelFeature = "文生图" | "图生图" | "多图参考" | "图像编辑";

export type ImageModelInfo = {
  id: ImageModel;
  label: string;
  features: ImageModelFeature[];
};

export const IMAGE_MODEL_CATALOG: ImageModelInfo[] = [
  { id: "doubao-seedream-5-0-pro-260628", label: "即梦 5.0 Pro", features: ["文生图", "图生图"] },
  { id: "banana-2", label: "Nano Banana 2", features: ["文生图", "图生图"] },
  { id: "doubao-seedream-5-0-260128", label: "即梦 5.0", features: ["文生图", "图生图"] },
  { id: "wan2.7-image", label: "万相 2.7 图像", features: ["文生图", "图生图"] },
  { id: "doubao-seedream-4-5-251128", label: "即梦 4.5", features: ["文生图", "图生图"] },
  { id: "kling-v3-omni", label: "可灵-V3-Omni", features: ["文生图", "图生图"] },
  { id: "qwen-image", label: "千问-image-max", features: ["文生图", "图像编辑"] },
  { id: "kling-v3", label: "可灵-V3", features: ["文生图", "图生图"] },
  { id: "wan2.6-image", label: "万相 2.6 图像", features: ["文生图", "图生图"] },
  { id: "kling-image-o1", label: "可灵 o1", features: ["文生图", "图生图"] },
  { id: "gpt-image-2", label: "GPT Image 2", features: ["文生图", "图生图"] },
];

const IMAGE_MODEL_BY_ID = new Map(IMAGE_MODEL_CATALOG.map((model) => [model.id, model]));
const HIDDEN_IMAGE_MODEL_IDS = new Set([
  "doubao-seedream-5-0-pro-260628",
  "doubao-seedream-5-0-260128",
  "doubao-seedream-4-5-251128",
  "gemini-3.1-flash-image-preview",
  "qwen-image",
  "vidu-image-2",
  "mj_imagine",
  "wan2.7-image",
  "kling-v3-omni",
  "kling-v3",
  "wan2.6-image",
  "kling-image-o1",
]);
const HIDDEN_IMAGE_MODEL_PATTERNS = [
  /seedream/i,
  /\bdoubao\b/i,
  /\bqwen\b/i,
  /\bvidu\b/i,
  /^mj[_-]?imagine$/i,
];
const IMAGE_MODEL_PATTERNS = [
  /image/i,
  /banana/i,
  /^mj[_-]?imagine$/i,
  /^kling/i,
  /^wan\d+(?:\.\d+)?[-_]image/i,
];

export const BUILTIN_IMAGE_MODELS: ImageModel[] = IMAGE_MODEL_CATALOG
  .map((model) => model.id)
  .filter((model) => !HIDDEN_IMAGE_MODEL_IDS.has(model));

export function imageModelInfo(model: ImageModel | string): ImageModelInfo | undefined {
  return IMAGE_MODEL_BY_ID.get(String(model || "").trim());
}

export function formatImageModel(model: ImageModel | string) {
  const value = String(model || "").trim();
  return imageModelInfo(value)?.label || value;
}

export function imageModelFeatures(model: ImageModel | string): ImageModelFeature[] {
  return imageModelInfo(model)?.features || ["文生图", "图生图"];
}

export function isImageModel(model: string) {
  const value = String(model || "").trim();
  if (HIDDEN_IMAGE_MODEL_IDS.has(value) || HIDDEN_IMAGE_MODEL_PATTERNS.some((pattern) => pattern.test(value))) return false;
  if (!value || /(?:^|[-_])guan(?:$|[-_])/i.test(value)) return false;
  return Boolean(IMAGE_MODEL_BY_ID.has(value) || IMAGE_MODEL_PATTERNS.some((pattern) => pattern.test(value)));
}

export function filterImageModels(items: Model[]) {
  const relayModels = items.map((item) => String(item.id || "").trim()).filter(isImageModel);
  return Array.from(new Set([...BUILTIN_IMAGE_MODELS, ...relayModels]));
}
