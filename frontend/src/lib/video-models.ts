import type { VideoGenerationModelSpec } from "@/lib/api";

export type VideoModelOption = {
  value: string;
  label: string;
};

export const DEFAULT_VIDEO_MODEL = "hailuo-h3";

export const VIDEO_MODEL_CATALOG: VideoGenerationModelSpec[] = [
  {
    id: "hailuo-h3",
    label: "海螺 H3",
    modes: ["text_to_video"],
    min_images: 0,
    max_images: 0,
    image_input_kind: "none",
    aspect_ratios: ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
    durations: Array.from({ length: 12 }, (_, index) => index + 4),
    default_duration: 5,
    option_key: "resolution",
    options: ["768P", "1080P", "2K"],
    default_option: "768P",
  },
  {
    id: "doubao-seedance-2-5-260628",
    label: "豆包 SD2.5",
    modes: ["text_to_video"],
    min_images: 0,
    max_images: 0,
    image_input_kind: "none",
    aspect_ratios: ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
    durations: Array.from({ length: 27 }, (_, index) => index + 4),
    default_duration: 5,
    option_key: "resolution",
    options: ["480p", "720p"],
    default_option: "480p",
  },
  {
    id: "kling-v3-video",
    label: "可灵 V3",
    modes: ["text_to_video"],
    min_images: 0,
    max_images: 0,
    image_input_kind: "none",
    aspect_ratios: ["16:9", "9:16", "1:1"],
    durations: [5, 10, 15],
    default_duration: 5,
    option_key: "mode",
    options: ["std", "pro"],
    default_option: "std",
  },
  {
    id: "hailuo-h3-cankaosheng",
    label: "海螺 H3 参考生",
    modes: ["image_to_video"],
    min_images: 1,
    max_images: 9,
    image_input_kind: "reference",
    aspect_ratios: ["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
    durations: Array.from({ length: 12 }, (_, index) => index + 4),
    default_duration: 5,
    option_key: "resolution",
    options: ["768P", "1080P", "2K", "4K"],
    default_option: "768P",
  },
  {
    id: "hailuo-h3-max-shouweizhen",
    label: "海螺 H3 Max 首尾帧",
    modes: ["image_to_video"],
    min_images: 1,
    max_images: 2,
    image_input_kind: "first_last_frame",
    aspect_ratios: ["adaptive"],
    durations: Array.from({ length: 11 }, (_, index) => index + 5),
    default_duration: 5,
    option_key: "resolution",
    options: ["480P", "768P"],
    default_option: "480P",
  },
  {
    id: "gk-video-3.5",
    label: "GK-video-3.5",
    modes: ["image_to_video"],
    min_images: 1,
    max_images: 1,
    image_input_kind: "reference",
    aspect_ratios: ["16:9", "9:16", "1:1", "3:2", "2:3"],
    durations: Array.from({ length: 15 }, (_, index) => index + 1),
    default_duration: 5,
    option_key: "resolution",
    options: ["720p", "480p"],
    default_option: "720p",
  },
  {
    id: "doubao-seedance-2-5-cankaosheng",
    label: "SD 2.5 参考生",
    modes: ["image_to_video"],
    min_images: 1,
    max_images: 30,
    image_input_kind: "reference",
    aspect_ratios: ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
    durations: ["auto", ...Array.from({ length: 27 }, (_, index) => index + 4)],
    default_duration: "auto",
    option_key: "resolution",
    options: ["480p", "720p"],
    default_option: "480p",
  },
];

export function normalizeVideoModelSpecs(items?: VideoGenerationModelSpec[]) {
  const source = items?.length ? items : VIDEO_MODEL_CATALOG;
  return source.filter((item) => item.id && item.label && item.aspect_ratios?.length && item.durations?.length && item.options?.length);
}

export function formatVideoModelOption(value: string, spec: VideoGenerationModelSpec) {
  if (spec.option_key === "mode") {
    const labels: Record<string, string> = {
      std: "标准",
      pro: "高品质",
    };
    return labels[value] || value;
  }
  return value;
}

export function videoModelOptionLabel(spec: VideoGenerationModelSpec) {
  return spec.option_key === "mode" ? "模式" : "清晰度";
}

export function formatVideoAspectRatio(value: string) {
  return value === "adaptive" ? "自适应" : value;
}

export function videoModelUsesFirstLastFrames(spec?: VideoGenerationModelSpec | null) {
  return spec?.image_input_kind === "first_last_frame";
}

export function videoFrameRole(spec: VideoGenerationModelSpec | null | undefined, index: number) {
  if (!videoModelUsesFirstLastFrames(spec)) return "";
  if (index === 0) return "首帧";
  if (index === 1) return "尾帧";
  return `第 ${index + 1} 帧`;
}

export function formatVideoDuration(value: number | "auto") {
  return value === "auto" ? "自动" : `${value}s`;
}
