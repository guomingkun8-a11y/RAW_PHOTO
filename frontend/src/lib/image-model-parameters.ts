export const IMAGE_26_SIZES = ["auto", "1024x1024", "1536x1024", "1024x1536"];
export const IMAGE_26_RATIOS = ["auto", "1:1", "3:2", "2:3"];
export const IMAGE_26_QUALITIES = ["auto", "low", "medium", "high", "xhigh", "max"];
export const IMAGE_26_COMPLEXITIES = ["low", "medium", "high"];
export const IMAGE_26_OUTPUT_FORMATS = ["png", "jpeg", "webp", "svg", "gif"];
export const IMAGE_26_BACKGROUNDS = ["auto", "opaque", "transparent"];
export const IMAGE_26_FRAME_COUNTS = [4, 6, 9, 12, 16];
export const IMAGE_26_FRAME_DURATIONS = [100, 200, 300, 500, 1000];

export type Image26Options = {
  complexity?: string;
  outputFormat?: string;
  background?: string;
  frameCount?: number;
  frameDurationMs?: number;
};

export function isImage26Model(model: string | undefined) {
  return /^(?:gpt|tt)-image-2\.6(?:-(?:flare|sunburst))?$/i.test(model || "");
}

export function normalizeImage26Options(options: Image26Options = {}): Image26Options {
  const outputFormat = IMAGE_26_OUTPUT_FORMATS.includes(options.outputFormat || "") ? options.outputFormat! : "png";
  return {
    complexity: IMAGE_26_COMPLEXITIES.includes(options.complexity || "") ? options.complexity : "medium",
    outputFormat,
    background: IMAGE_26_BACKGROUNDS.includes(options.background || "") ? options.background : "auto",
    ...(outputFormat === "gif" ? {
      frameCount: IMAGE_26_FRAME_COUNTS.includes(Number(options.frameCount)) ? Number(options.frameCount) : 9,
      frameDurationMs: IMAGE_26_FRAME_DURATIONS.includes(Number(options.frameDurationMs)) ? Number(options.frameDurationMs) : 200,
    } : {}),
  };
}

export function image26RequestFields(model: string | undefined, options: Image26Options = {}) {
  if (!isImage26Model(model)) return {};
  const normalized = normalizeImage26Options(options);
  return {
    complexity: normalized.complexity,
    output_format: normalized.outputFormat,
    background: normalized.background,
    ...(normalized.outputFormat === "gif" ? {
      frame_count: normalized.frameCount,
      frame_duration_ms: normalized.frameDurationMs,
    } : {}),
  };
}

export function image26Quality(model: string | undefined, quality: string) {
  return isImage26Model(model) && !IMAGE_26_QUALITIES.includes(quality) ? "auto" : quality;
}

export function image26MimeType(outputFormat: string | undefined) {
  return ({ jpeg: "image/jpeg", webp: "image/webp", svg: "image/svg+xml", gif: "image/gif" } as Record<string, string>)[outputFormat || ""] || "image/png";
}

export function image26Extension(outputFormat: string | undefined) {
  return ({ jpeg: "jpg", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg", "image/gif": "gif" } as Record<string, string>)[outputFormat || ""] || (outputFormat || "png");
}

// Match source and prompt ratios to the three native sizes; 2.6 has no resolution tier.
export function image26Size(size: string | undefined): string {
  if (!size || size === "auto" || size === "adaptive") return "auto";
  if (IMAGE_26_SIZES.includes(size)) return size;
  const match = size.match(/^(\d+(?:\.\d+)?)[x:](\d+(?:\.\d+)?)$/);
  if (!match || Number(match[1]) <= 0 || Number(match[2]) <= 0) return "auto";
  const ratio = Number(match[1]) / Number(match[2]);
  return IMAGE_26_SIZES.slice(1).reduce((best, candidate) => {
    const [width, height] = candidate.split("x").map(Number);
    const [bestWidth, bestHeight] = best.split("x").map(Number);
    return Math.abs(width / height - ratio) < Math.abs(bestWidth / bestHeight - ratio) ? candidate : best;
  });
}

export function image26Ratio(size: string) {
  return IMAGE_26_RATIOS[IMAGE_26_SIZES.indexOf(image26Size(size))] || "auto";
}
