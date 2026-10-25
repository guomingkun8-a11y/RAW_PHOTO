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
    id: "seedance-2.0-guanfang",
    label: "Seedance 2.0 官方全能参考",
    modes: ["text_to_video", "image_to_video", "video_to_video"],
    min_images: 0,
    max_images: 9,
    max_videos: 3,
    max_audios: 3,
    min_media_inputs: 0,
    allow_audio_only: false,
    image_input_kind: "reference",
    image_input_param: "image_url",
    video_input_kind: "reference",
    video_input_param: "video_url",
    video_input_shape: "list",
    audio_input_kind: "reference",
    audio_input_param: "audio_url",
    audio_input_shape: "list",
    request_mode_param: "mode",
    request_mode_options: ["shouweizhen", "cankaosheng"],
    request_mode_default: "cankaosheng",
    request_mode_labels: { shouweizhen: "首尾帧", cankaosheng: "参考生" },
    media_by_request_mode: {
      shouweizhen: {
        image_input_kind: "first_last_frame",
        image_input_param: "images",
        max_images: 2,
        max_videos: 0,
        max_audios: 0,
      },
      cankaosheng: {
        image_input_kind: "reference",
        image_input_param: "image_url",
        max_images: 9,
        max_videos: 3,
        max_audios: 3,
      },
    },
    reference_mode_param: "_quan_neng_mode",
    reference_mode_options: ["quan_neng", "edit", "extend"],
    reference_mode_default: "quan_neng",
    reference_mode_labels: { quan_neng: "全能参考", edit: "编辑视频", extend: "延长视频" },
    aspect_ratios: ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
    durations: ["auto", ...Array.from({ length: 12 }, (_, index) => index + 4)],
    default_duration: "auto",
    option_key: "resolution",
    options: ["480p", "720p", "1080p", "4K"],
    default_option: "720p",
    secondary_option_key: "version",
    secondary_options: ["Mini", "快速", "标准"],
    secondary_default_option: "标准",
    options_by_secondary: {
      Mini: ["480p", "720p"],
      快速: ["480p", "720p"],
      标准: ["480p", "720p", "1080p", "4K"],
    },
  },
  {
    id: "doubao-seedance-2-5-quannengcankao",
    label: "SD 2.5 全能参考",
    modes: ["text_to_video", "image_to_video", "video_to_video"],
    min_images: 0,
    max_images: 30,
    max_videos: 10,
    max_audios: 10,
    min_media_inputs: 1,
    allow_audio_only: true,
    image_input_kind: "reference",
    image_input_param: "image_url",
    video_input_kind: "reference",
    video_input_param: "video_url",
    video_input_shape: "list",
    audio_input_kind: "reference",
    audio_input_param: "audio_url",
    audio_input_shape: "list",
    aspect_ratios: ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
    durations: ["auto", ...Array.from({ length: 27 }, (_, index) => index + 4)],
    default_duration: "auto",
    option_key: "resolution",
    options: ["480p", "720p", "1080p"],
    default_option: "480p",
  },
  {
    id: "wan3.0-video-quannengcankao",
    label: "万相 3.0 全能参考",
    modes: ["text_to_video", "image_to_video", "video_to_video"],
    min_images: 0,
    max_images: 10,
    max_videos: 5,
    max_audios: 5,
    allow_audio_only: true,
    image_input_kind: "reference",
    image_input_param: "image_url",
    video_input_kind: "reference",
    video_input_param: "video_url",
    video_input_shape: "list",
    audio_input_kind: "reference",
    audio_input_param: "audio_url",
    audio_input_shape: "list",
    aspect_ratio_param: "ratio",
    aspect_ratios: ["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4"],
    durations: ["auto", ...Array.from({ length: 29 }, (_, index) => index + 2)],
    default_duration: 5,
    option_key: "resolution",
    options: ["480P", "720P", "1080P"],
    default_option: "720P",
    secondary_option_key: "version",
    secondary_options: ["standard", "prime"],
    secondary_default_option: "standard",
  },
  {
    id: "hailuo-h3-quannengcankao",
    label: "海螺 H3 全能参考",
    modes: ["image_to_video", "video_to_video"],
    min_images: 0,
    max_images: 9,
    max_videos: 3,
    max_audios: 3,
    min_media_inputs: 1,
    allow_audio_only: false,
    image_input_kind: "reference",
    image_input_param: "image_url",
    video_input_kind: "reference",
    video_input_param: "video_url",
    video_input_shape: "list",
    audio_input_kind: "reference",
    audio_input_param: "audio_url",
    audio_input_shape: "list",
    aspect_ratios: ["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
    durations: Array.from({ length: 12 }, (_, index) => index + 4),
    default_duration: 5,
    option_key: "resolution",
    options: ["768P", "1080P", "2K", "4K"],
    default_option: "768P",
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
  {
    id: "wan2.7-cankaosheng",
    label: "万相 2.7 参考生",
    modes: ["text_to_video", "image_to_video", "video_to_video"],
    min_images: 0,
    max_images: 5,
    image_input_kind: "reference",
    image_input_param: "reference_urls",
    video_input_kind: "reference",
    video_input_param: "reference_video",
    video_input_shape: "scalar",
    max_videos: 1,
    aspect_ratio_param: "ratio",
    mode_param: "_mode",
    mode_value: "wan2.7-r2v",
    aspect_ratios: ["16:9", "9:16", "1:1", "4:3", "3:4"],
    durations: [3, 6, 9, 12, 15],
    default_duration: 3,
    option_key: "resolution",
    options: ["720P", "1080P"],
    default_option: "720P",
  },
];

export function normalizeVideoModelSpecs(items?: VideoGenerationModelSpec[]) {
  const source = items?.length ? items : VIDEO_MODEL_CATALOG;
  return source.filter((item) => item.id && item.label && item.aspect_ratios?.length && item.durations?.length && item.options?.length);
}

export function selectVideoModelForMode(
  specs: VideoGenerationModelSpec[],
  mode: "text_to_video" | "image_to_video" | "video_to_video" | string,
  selectedId = "",
) {
  const available = specs.filter((item) => item.modes.includes(mode));
  const preferredId = mode === "video_to_video"
    ? "wan2.7-cankaosheng"
    : mode === "image_to_video"
      ? "hailuo-h3-cankaosheng"
      : "hailuo-h3";
  return available.find((item) => item.id === selectedId)
    || available.find((item) => item.id === preferredId)
    || available[0];
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

export function videoModelMediaSpec(spec?: VideoGenerationModelSpec | null, requestMode = "") {
  if (!spec) return undefined;
  const override = requestMode ? spec.media_by_request_mode?.[requestMode] : undefined;
  return override ? { ...spec, ...override } : spec;
}

export function videoModelOptionsForSecondary(
  spec: VideoGenerationModelSpec | null | undefined,
  secondaryValue = "",
) {
  if (!spec) return [];
  const configured = secondaryValue ? spec.options_by_secondary?.[secondaryValue] : undefined;
  return configured?.length ? configured : spec.options;
}

export function videoModelUsesFirstLastFramesForMode(spec?: VideoGenerationModelSpec | null, requestMode = "") {
  return videoModelMediaSpec(spec, requestMode)?.image_input_kind === "first_last_frame";
}

export function videoFrameRole(
  spec: VideoGenerationModelSpec | null | undefined,
  index: number,
  requestMode = "",
) {
  if (!videoModelUsesFirstLastFramesForMode(spec, requestMode)) return "";
  if (index === 0) return "首帧";
  if (index === 1) return "尾帧";
  return `第 ${index + 1} 帧`;
}

export function formatVideoDuration(value: number | "auto") {
  return value === "auto" ? "自动" : `${value}s`;
}
