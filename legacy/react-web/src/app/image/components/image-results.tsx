"use client";

import { memo, useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import {
  CircleStop,
  Clock3,
  Download,
  EyeOff,
  Heart,
  Maximize2,
  RotateCcw,
  Sparkles,
  Trash2,
  WandSparkles,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { downloadImageTaskZip } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ImageConversation, ImageTurnStatus, StoredImage, StoredReferenceImage } from "@/store/image-conversations";

export type ImageLightboxItem = {
  id: string;
  src: string;
  sizeLabel?: string;
  dimensions?: string;
};

type ImageResultsProps = {
  selectedConversation: ImageConversation | null;
  onOpenLightbox: (images: ImageLightboxItem[], index: number) => void;
  onContinueEdit: (conversationId: string, image: StoredImage | StoredReferenceImage) => void;
  onDeletePrompt: (conversationId: string, turnId: string) => void;
  onDeleteResults: (conversationId: string, turnId: string) => void;
  onReuseTurnConfig: (conversationId: string, turnId: string) => void | Promise<void>;
  onRegenerateTurn: (conversationId: string, turnId: string) => void | Promise<void>;
  onRetryImage: (conversationId: string, turnId: string, imageId: string) => void | Promise<void>;
  onCancelTurn: (conversationId: string, turnId: string) => void | Promise<void>;
  onTimeoutRetryContinue: (taskId: string) => void | Promise<void>;
  allowTimeoutRetryContinue: boolean;
  onDismissErrors: (conversationId: string, turnId: string) => void | Promise<void>;
  formatConversationTime: (value: string) => string;
};

const b64BlobUrlCache = new Map<string, string>();
const crc32Table = new Uint32Array(256).map((_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

const AI_PROGRESS_STEPS = [
  { label: "🧠 理解商品...", bar: "████████" },
  { label: "🎨 分析风格...", bar: "██████" },
  { label: "✨ 构图中...", bar: "████" },
  { label: "🖼️ 渲染中...", bar: "██" },
  { label: "💎 AI精修...", bar: "█" },
];
const AI_PROGRESS_STEP_REVEAL_MS = 2400;

function getStoredImageSrc(image: StoredImage) {
  if (image.b64_json) {
    let url = b64BlobUrlCache.get(image.b64_json);
    if (!url) {
      const binary = atob(image.b64_json);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: "image/png" });
      url = URL.createObjectURL(blob);
      b64BlobUrlCache.set(image.b64_json, url);
    }
    return url;
  }
  return image.url || "";
}

async function downloadStoredImage(image: StoredImage, index: number) {
  const blob = await getStoredImageBlob(image);
  if (!blob) {
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `image-${index + 1}.png`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function getStoredImageBlob(image: StoredImage) {
  let blob: Blob | null = null;
  try {
    if (image.b64_json) {
      const binary = atob(image.b64_json);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      blob = new Blob([bytes], { type: "image/png" });
    } else if (image.url) {
      const url = image.url.startsWith("http") ? image.url : `${window.location.origin}${image.url}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      blob = await res.blob();
    } else {
      return;
    }
  } catch (err) {
    console.error("Failed to download image:", err);
    if (image.url) {
      window.open(image.url, "_blank");
    }
    return;
  }
  return blob;
}

function sanitizeZipName(value: string) {
  return value
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80) || "image";
}

function imageExtension(blob: Blob, image: StoredImage) {
  const source = `${image.sourceName || image.url || ""}`.toLowerCase();
  const match = source.match(/\.(png|jpe?g|webp|gif|avif)(?:$|\?)/i);
  if (match) {
    return match[1].replace("jpeg", "jpg");
  }
  if (blob.type.includes("jpeg")) return "jpg";
  if (blob.type.includes("webp")) return "webp";
  if (blob.type.includes("gif")) return "gif";
  if (blob.type.includes("avif")) return "avif";
  return "png";
}

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = crc32Table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function writeUint16(target: Uint8Array, offset: number, value: number) {
  target[offset] = value & 0xff;
  target[offset + 1] = (value >>> 8) & 0xff;
}

function writeUint32(target: Uint8Array, offset: number, value: number) {
  target[offset] = value & 0xff;
  target[offset + 1] = (value >>> 8) & 0xff;
  target[offset + 2] = (value >>> 16) & 0xff;
  target[offset + 3] = (value >>> 24) & 0xff;
}

async function createZipBlob(files: Array<{ path: string; blob: Blob }>) {
  const encoder = new TextEncoder();
  const localChunks: Uint8Array[] = [];
  const centralChunks: Uint8Array[] = [];
  let offset = 0;

  for (const file of files) {
    const name = encoder.encode(file.path);
    const data = new Uint8Array(await file.blob.arrayBuffer());
    const checksum = crc32(data);
    const local = new Uint8Array(30 + name.length);
    writeUint32(local, 0, 0x04034b50);
    writeUint16(local, 4, 20);
    writeUint16(local, 6, 0x0800);
    writeUint16(local, 8, 0);
    writeUint32(local, 14, checksum);
    writeUint32(local, 18, data.length);
    writeUint32(local, 22, data.length);
    writeUint16(local, 26, name.length);
    local.set(name, 30);
    localChunks.push(local, data);

    const central = new Uint8Array(46 + name.length);
    writeUint32(central, 0, 0x02014b50);
    writeUint16(central, 4, 20);
    writeUint16(central, 6, 20);
    writeUint16(central, 8, 0x0800);
    writeUint16(central, 10, 0);
    writeUint32(central, 16, checksum);
    writeUint32(central, 20, data.length);
    writeUint32(central, 24, data.length);
    writeUint16(central, 28, name.length);
    writeUint32(central, 42, offset);
    central.set(name, 46);
    centralChunks.push(central);

    offset += local.length + data.length;
  }

  const centralSize = centralChunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const end = new Uint8Array(22);
  writeUint32(end, 0, 0x06054b50);
  writeUint16(end, 8, files.length);
  writeUint16(end, 10, files.length);
  writeUint32(end, 12, centralSize);
  writeUint32(end, 16, offset);

  const zipParts = [...localChunks, ...centralChunks, end].map((chunk) =>
    chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength) as ArrayBuffer,
  );
  return new Blob(zipParts, { type: "application/zip" });
}

async function downloadTurnZip(turnId: string, images: StoredImage[]) {
  const successImages = images.filter((image) => image.status === "success" && (image.b64_json || image.url));
  if (successImages.length === 0) {
    return;
  }
  const folderName = sanitizeZipName(`AI-Image-Results-${turnId.slice(0, 8)}`);
  const apiItems = successImages.map((image, index) => ({
    url: image.url,
    b64Json: image.b64_json,
    filename: sanitizeZipName(image.sourceName ? `${index + 1}-${image.sourceName}` : `image-${index + 1}.png`),
  }));
  try {
    const zipBlob = await downloadImageTaskZip({ folderName, items: apiItems });
    const url = URL.createObjectURL(zipBlob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${folderName}.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return;
  } catch (error) {
    console.error("Server zip download failed, falling back to browser zip:", error);
  }

  const files: Array<{ path: string; blob: Blob }> = [];
  for (const [index, image] of successImages.entries()) {
    const blob = await getStoredImageBlob(image);
    if (!blob) {
      continue;
    }
    const baseName = sanitizeZipName(image.sourceName ? `${index + 1}-${image.sourceName.replace(/\.[^.]+$/, "")}` : `image-${index + 1}`);
    files.push({
      path: `${folderName}/${baseName}.${imageExtension(blob, image)}`,
      blob,
    });
  }
  if (files.length === 0) {
    return;
  }
  const zipBlob = await createZipBlob(files);
  const url = URL.createObjectURL(zipBlob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${folderName}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function ImageResults({
  selectedConversation,
  onOpenLightbox,
  onContinueEdit,
  onDeletePrompt,
  onDeleteResults,
  onReuseTurnConfig,
  onRegenerateTurn,
  onRetryImage,
  onCancelTurn,
  onTimeoutRetryContinue,
  allowTimeoutRetryContinue,
  onDismissErrors,
  formatConversationTime,
}: ImageResultsProps) {
  const imageDimensionsRef = useRef<Record<string, string>>({});
  const [currentTime, setCurrentTime] = useState(Date.now());
  const [downloadingTurnIds, setDownloadingTurnIds] = useState<Set<string>>(() => new Set());

  const hasLoadingImages = selectedConversation?.turns.some(
    (turn) => !turn.resultsDeleted && turn.images.some((image) => image.status === "loading"),
  );

  useEffect(() => {
    if (!hasLoadingImages) return;
    const timer = setInterval(() => {
      setCurrentTime(Date.now());
    }, 500);
    return () => clearInterval(timer);
  }, [hasLoadingImages]);

  const updateImageDimensions = (id: string, width: number, height: number) => {
    const dimensions = formatImageDimensions(width, height);
    if (imageDimensionsRef.current[id] !== dimensions) {
      imageDimensionsRef.current[id] = dimensions;
    }
  };

  const handleDownloadTurnZip = async (turnId: string, images: StoredImage[]) => {
    if (downloadingTurnIds.has(turnId)) {
      return;
    }
    setDownloadingTurnIds((current) => new Set(current).add(turnId));
    try {
      await downloadTurnZip(turnId, images);
    } finally {
      setDownloadingTurnIds((current) => {
        const next = new Set(current);
        next.delete(turnId);
        return next;
      });
    }
  };

  if (!selectedConversation) {
    return <EmptyCreativeState />;
  }

  return (
    <div className="mx-auto flex w-full max-w-[1380px] flex-col gap-5 pb-6">
      {selectedConversation.turns.map((turn, turnIndex) => {
        const referenceLightboxImages = turn.referenceImages.map((image, index) => ({
          id: `${turn.id}-reference-${index}`,
          src: image.dataUrl,
        }));
        const successfulTurnImages = turn.images.flatMap((image) => {
          const src = image.status === "success" ? getStoredImageSrc(image) : "";
          return src
            ? [
                {
                  id: image.id,
                  src,
                  sizeLabel: image.b64_json ? formatBase64ImageSize(image.b64_json) : undefined,
                  dimensions: imageDimensionsRef.current[image.id],
                },
              ]
            : [];
        });
        const successImageCount = successfulTurnImages.length;
        const isDownloadingTurn = downloadingTurnIds.has(turn.id);
        const hasLoadingTurnImages = turn.images.some((image) => image.status === "loading");

        return (
          <section
            key={turn.id}
            data-scroll-preview={`第 ${turnIndex + 1} 轮 · ${turn.images.length} 张`}
            className="studio-card overflow-hidden bg-white dark:bg-[#171a21]"
          >
            {!turn.promptDeleted ? (
              <div className="border-b border-black/[0.06] bg-white px-4 py-4 dark:border-white/10 dark:bg-white/[0.03] sm:px-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-[12px] text-slate-500 dark:text-stone-400">
                      <span className="font-semibold text-slate-950 dark:text-stone-50">第 {turnIndex + 1} 轮</span>
                      <span className="rounded-full border border-black/[0.06] bg-slate-50 px-2 py-1 dark:border-white/10 dark:bg-white/[0.06]">
                        {turn.mode === "edit" ? "图生图" : "文生图"}
                      </span>
                      <span className={cn("rounded-full border px-2 py-1 font-semibold", getTurnStatusClass(turn.status))}>
                        {turn.status === "canceled" ? "已中止" : getTurnStatusLabel(turn.status)}
                      </span>
                      <span>{formatConversationTime(turn.createdAt)}</span>
                    </div>
                    <p className="mt-3 max-w-[82ch] whitespace-pre-wrap text-[15px] leading-7 text-slate-700 dark:text-stone-200">
                      {turn.prompt}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {hasLoadingTurnImages ? (
                      <Button
                        type="button"
                        variant="outline"
                        className="studio-button h-9 rounded-2xl border-rose-200 bg-white px-3 text-[12px] font-semibold text-rose-600 shadow-none hover:bg-rose-50 dark:border-rose-500/30 dark:bg-white/[0.04] dark:text-rose-200 dark:hover:bg-rose-950/30"
                        onClick={() => void onCancelTurn(selectedConversation.id, turn.id)}
                      >
                        <CircleStop className="size-3.5" />
                        停止生成
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      className="studio-button h-9 rounded-2xl border-black/[0.06] bg-white px-3 text-[12px] shadow-none"
                      onClick={() => void onReuseTurnConfig(selectedConversation.id, turn.id)}
                    >
                      <WandSparkles className="size-3.5" />
                      复用配置
                    </Button>
                    <button
                      type="button"
                      onClick={() => onDeletePrompt(selectedConversation.id, turn.id)}
                      className="studio-button inline-flex size-9 items-center justify-center rounded-2xl text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:text-stone-500 dark:hover:bg-rose-950/30 dark:hover:text-rose-300"
                      aria-label="删除提示词记录"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                </div>
              </div>
            ) : null}

            {!turn.resultsDeleted ? (
              <div className="px-4 py-4 sm:px-5 sm:py-5">
                {turn.referenceImages.length > 0 ? (
                  <div className="mb-5 rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] px-4 py-4 dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="mb-3 text-[13px] font-semibold text-slate-600 dark:text-stone-300">本轮参考图</div>
                    <div className="flex flex-wrap gap-3">
                      {turn.referenceImages.map((image, index) => (
                        <div key={`${turn.id}-${image.name}-${index}`} className="flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() => onOpenLightbox(referenceLightboxImages, index)}
                            className="group relative h-24 w-24 overflow-hidden rounded-2xl border border-black/[0.06] bg-white text-left transition hover:scale-[1.02] hover:border-[#4F7CFF]/25 dark:border-white/10 dark:bg-[#111317]"
                            aria-label={`预览参考图 ${image.name || index + 1}`}
                          >
                            <img
                              src={image.dataUrl}
                              alt={image.name || `参考图 ${index + 1}`}
                              className="absolute inset-0 h-full w-full object-cover transition duration-300 group-hover:scale-105"
                            />
                          </button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="studio-button h-8 rounded-xl border-black/[0.06] bg-white text-xs text-slate-700 shadow-none hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04]"
                            onClick={() => onContinueEdit(selectedConversation.id, image)}
                          >
                            <Sparkles className="size-3.5" />
                            加入编辑
                          </Button>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="mb-4 flex flex-wrap items-center justify-between gap-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2 text-slate-500 dark:text-stone-400">
                    <span className="rounded-full border border-black/[0.06] bg-slate-50 px-2.5 py-1 dark:border-white/10 dark:bg-white/[0.04]">{turn.count} 张</span>
                    <span className={cn("rounded-full border px-2.5 py-1 font-semibold", getTurnStatusClass(turn.status))}>
                      {turn.status === "canceled" ? "已中止" : getTurnStatusLabel(turn.status)}
                    </span>
                    {turn.status === "queued" ? (
                      <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-amber-700 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200">
                        等待前序任务完成
                      </span>
                    ) : null}
                  </div>
                </div>

                <div className="columns-1 gap-4 space-y-4 sm:columns-2 xl:columns-3">
                  {turn.images.map((image, index) => {
                    const imageSrc = image.status === "success" ? getStoredImageSrc(image) : "";
                    if (image.status === "success" && imageSrc) {
                      const currentIndex = successfulTurnImages.findIndex((item) => item.id === image.id);
                      const sizeLabel = image.b64_json ? formatBase64ImageSize(image.b64_json) : "";
                      const dimensions = imageDimensionsRef.current[image.id];
                      const sourceLabel = image.sourceName ? `源图 ${image.sourceName}` : "";
                      const imageMeta = [sourceLabel, sizeLabel, dimensions].filter(Boolean).join(" / ");

                      return (
                        <div
                          key={image.id}
                          className="group relative break-inside-avoid overflow-hidden rounded-[20px] border border-black/[0.06] bg-white shadow-[0_16px_40px_rgba(15,23,42,0.08)] transition duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-1 hover:border-[#4F7CFF]/25 hover:shadow-[0_24px_70px_rgba(15,23,42,0.14)] dark:border-white/10 dark:bg-[#171a21]"
                        >
                          <LazyImage
                            src={imageSrc}
                            alt={`Generated result ${index + 1}`}
                            className="generated-reveal group block w-full cursor-zoom-in overflow-hidden bg-slate-100 dark:bg-[#111317]"
                            onLoad={(event) => {
                              updateImageDimensions(
                                image.id,
                                event.currentTarget.naturalWidth,
                                event.currentTarget.naturalHeight,
                              );
                            }}
                            onOpen={() => onOpenLightbox(successfulTurnImages, currentIndex)}
                          />
                          {image.qualityCheck ? (
                            <div className={cn("absolute left-3 top-3 z-10 rounded-full border px-2.5 py-1 text-[11px] font-semibold shadow-sm backdrop-blur", getQualityCheckClass(image.qualityCheck.status))}>
                              {getQualityCheckLabel(image.qualityCheck.status)}
                              {typeof image.qualityCheck.score === "number" ? ` ${image.qualityCheck.score}` : ""}
                            </div>
                          ) : null}
                          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-slate-950/72 via-slate-950/0 to-transparent opacity-0 transition duration-300 group-hover:opacity-100" />
                          <div className="absolute inset-x-0 bottom-0 flex translate-y-3 flex-col gap-3 p-3 opacity-0 transition duration-300 group-hover:translate-y-0 group-hover:opacity-100">
                            <div className="rounded-2xl bg-white/92 p-3 text-[12px] leading-5 text-slate-700 shadow-lg backdrop-blur dark:bg-[#171a21]/92 dark:text-stone-200">
                              <div className="mb-1 flex items-center justify-between gap-2">
                                <span className="font-semibold text-slate-950 dark:text-stone-50">结果 {index + 1}</span>
                                {image.durationMs != null ? <span className="text-slate-400">{formatDuration(image.durationMs)}</span> : null}
                              </div>
                              <div className="truncate text-slate-500 dark:text-stone-400">{imageMeta || "AI Image"}</div>
                              {image.qualityCheck ? (
                                <div className="mt-2 rounded-xl bg-[#F8FAFC] px-2.5 py-2 dark:bg-white/[0.06]">
                                  <div className="flex items-center justify-between gap-2">
                                    <span className={cn("font-semibold", image.qualityCheck.status === "passed" ? "text-emerald-700 dark:text-emerald-300" : image.qualityCheck.status === "failed" ? "text-rose-700 dark:text-rose-300" : "text-amber-700 dark:text-amber-300")}>
                                      {getQualityCheckLabel(image.qualityCheck.status)}
                                    </span>
                                    {typeof image.qualityCheck.score === "number" ? <span>{image.qualityCheck.score}/100</span> : null}
                                  </div>
                                  {image.qualityCheck.summary ? (
                                    <p className="mt-1 line-clamp-2 text-slate-500 dark:text-stone-400">{image.qualityCheck.summary}</p>
                                  ) : null}
                                  {image.qualityCheck.issues?.length ? (
                                    <p className="mt-1 line-clamp-1 text-amber-700 dark:text-amber-300">
                                      {image.qualityCheck.issues[0]}
                                    </p>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                            <div className="flex items-center justify-end gap-2">
                              <button
                                type="button"
                                className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-700 shadow-lg transition hover:scale-105 hover:text-[#4F7CFF]"
                                onClick={() => onOpenLightbox(successfulTurnImages, currentIndex)}
                                aria-label="查看大图"
                              >
                                <Maximize2 className="size-4" />
                              </button>
                              <button
                                type="button"
                                className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-700 shadow-lg transition hover:scale-105 hover:text-[#4F7CFF]"
                                onClick={() => onContinueEdit(selectedConversation.id, image)}
                                aria-label="AI 优化"
                              >
                                <Sparkles className="size-4" />
                              </button>
                              <button
                                type="button"
                                className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-700 shadow-lg transition hover:scale-105 hover:text-rose-500"
                                aria-label="收藏"
                              >
                                <Heart className="size-4" />
                              </button>
                              <button
                                type="button"
                                className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-700 shadow-lg transition hover:scale-105 hover:text-[#4F7CFF]"
                                onClick={() => void downloadStoredImage(image, index)}
                                aria-label="下载"
                              >
                                <Download className="size-4" />
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    }

                    if (image.status === "canceled") {
                      return (
                        <div key={image.id} className="break-inside-avoid overflow-hidden rounded-[20px] border border-slate-200 bg-white shadow-sm dark:border-white/10 dark:bg-[#171a21]">
                          <div className="flex aspect-square min-h-52 flex-col items-center justify-center gap-3 bg-slate-50 px-5 py-6 text-center text-sm leading-6 text-slate-600 dark:bg-white/[0.04] dark:text-stone-300">
                            <div className="flex size-10 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 shadow-sm dark:border-white/10 dark:bg-[#171a21] dark:text-stone-300">
                              <CircleStop className="size-5" />
                            </div>
                            <p className="font-semibold text-slate-950 dark:text-stone-50">图片 {index + 1}/{turn.images.length} 已中止</p>
                            <span className="line-clamp-2">{image.error || "本次生成已停止，后续结果不会写入当前任务。"}</span>
                            <button
                              type="button"
                              onClick={() => void onRetryImage(selectedConversation.id, turn.id, image.id)}
                              className="rounded-xl border border-black/[0.06] bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-[#4F7CFF]/[0.08] hover:text-slate-950 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200 dark:hover:bg-white/[0.08]"
                            >
                              重新生成这一张
                            </button>
                          </div>
                        </div>
                      );
                    }

                    if (image.status === "error") {
                      const isTimeoutError = image.error?.includes("超时") && image.taskId;
                      return (
                        <div key={image.id} className="break-inside-avoid overflow-hidden rounded-[20px] border border-rose-200 bg-white shadow-sm dark:border-rose-500/30 dark:bg-[#171a21]">
                          <div className="flex aspect-square min-h-52 flex-col items-center justify-center gap-3 bg-rose-50 px-5 py-6 text-center text-sm leading-6 text-rose-700 dark:bg-rose-950/20 dark:text-rose-200">
                            <p className="font-semibold">图片 {index + 1}/{turn.images.length}</p>
                            <span className="line-clamp-3">{image.error || "生成失败"}</span>
                            <div className="flex flex-wrap items-center justify-center gap-2">
                              {allowTimeoutRetryContinue && isTimeoutError ? (
                                <button
                                  type="button"
                                  onClick={() => void onTimeoutRetryContinue(image.taskId!)}
                                  className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-200"
                                >
                                  继续等待
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => void onRetryImage(selectedConversation.id, turn.id, image.id)}
                                className="rounded-xl border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 dark:border-rose-500/30 dark:bg-white/[0.04] dark:text-rose-200 dark:hover:bg-rose-950/30"
                              >
                                重新生成这一张
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    }

                    const imageTaskStatus = image.taskStatus || (turn.status === "queued" ? "queued" : "running");
                    const showElapsed = imageTaskStatus === "running" && image.elapsedSecs != null;
                    const elapsedDisplay = showElapsed
                      ? formatElapsed(
                          image.elapsedUpdatedAt != null
                            ? image.elapsedSecs! + (currentTime - image.elapsedUpdatedAt!) / 1000
                            : image.elapsedSecs!,
                        )
                      : null;
                    const revealElapsedMs = image.startTime != null
                      ? Math.max(0, currentTime - image.startTime)
                      : Math.max(0, (image.elapsedSecs ?? 0) * 1000);
                    const activeStep = imageTaskStatus === "queued"
                      ? 0
                      : Math.min(AI_PROGRESS_STEPS.length - 1, Math.floor(revealElapsedMs / AI_PROGRESS_STEP_REVEAL_MS));
                    const activeProgressStep = AI_PROGRESS_STEPS[activeStep] ?? AI_PROGRESS_STEPS[0];

                    return (
                      <div key={image.id} className="break-inside-avoid overflow-hidden rounded-[20px] border border-black/[0.06] bg-white shadow-[0_16px_40px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-[#171a21]">
                        <div
                          className={cn(
                            "relative overflow-hidden bg-[#F8FAFC] dark:bg-[#111317]",
                            turn.ratio === "16:9" ? "aspect-video" : turn.ratio === "9:16" ? "aspect-[9/16]" : turn.ratio === "4:3" ? "aspect-[4/3]" : turn.ratio === "3:4" ? "aspect-[3/4]" : "aspect-square",
                          )}
                        >
                          <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_15%,rgba(79,124,255,.20),transparent_35%),radial-gradient(circle_at_80%_70%,rgba(109,94,247,.16),transparent_35%)]" />
                          <div className="relative flex h-full flex-col justify-center gap-3 px-5 py-6">
                            <div className="flex items-center justify-between text-[12px] font-semibold text-slate-600 dark:text-stone-300">
                              <span className="inline-flex items-center gap-2">
                                {imageTaskStatus === "queued" ? <Clock3 className="size-4" /> : <Sparkles className="size-4 ai-orbit text-[#4F7CFF]" />}
                                图片 {index + 1}/{turn.images.length}
                              </span>
                              {elapsedDisplay ? <span>{elapsedDisplay}</span> : <span>AI Studio</span>}
                            </div>
                            {image.sourceName ? (
                              <div className="truncate text-[12px] font-medium text-slate-500 dark:text-stone-400">
                                源图：{image.sourceName}
                              </div>
                            ) : null}
                            <div className="rounded-[18px] border border-white/80 bg-white/76 p-4 shadow-[0_18px_44px_rgba(15,23,42,0.08)] backdrop-blur dark:border-white/10 dark:bg-[#171a21]/76">
                              <div
                                key={activeProgressStep.label}
                                className="grid grid-cols-[minmax(112px,1fr)_auto] items-center gap-3 text-[12px] text-slate-950 transition duration-300 animate-in fade-in slide-in-from-bottom-1 dark:text-stone-50"
                                style={{ animationDuration: "260ms" }}
                              >
                                <span className="font-semibold">{activeProgressStep.label}</span>
                                <span className="font-mono tracking-[1px] text-[#4F7CFF]">{activeProgressStep.bar}</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {turn.status === "error" && turn.error ? (
                  <div className="mt-4 flex items-center justify-between rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200">
                    <span>{turn.error}</span>
                    <button
                      type="button"
                      onClick={() => void onDismissErrors(selectedConversation.id, turn.id)}
                      className="ml-3 inline-flex shrink-0 items-center gap-1 rounded-xl border border-amber-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-amber-800 transition hover:bg-amber-100 dark:border-amber-500/30 dark:bg-white/[0.04] dark:text-amber-200 dark:hover:bg-amber-950/40"
                    >
                      <EyeOff className="size-3.5" />
                      忽略错误
                    </button>
                  </div>
                ) : null}

                <div className="mt-4 flex items-center justify-end gap-2 border-t border-black/[0.06] pt-4 text-[12px] dark:border-white/10">
                  <button
                    type="button"
                    onClick={() => void handleDownloadTurnZip(turn.id, turn.images)}
                    disabled={successImageCount === 0 || isDownloadingTurn}
                    className="studio-button inline-flex h-9 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 font-semibold text-slate-600 hover:bg-[#4F7CFF]/[0.08] hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-45 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-300"
                  >
                    <Download className="size-3.5" />
                    {isDownloadingTurn ? "打包中..." : `打包下载${successImageCount > 0 ? ` ${successImageCount} 张` : ""}`}
                  </button>
                  <button
                    type="button"
                    onClick={() => void onRegenerateTurn(selectedConversation.id, turn.id)}
                    className="studio-button inline-flex h-9 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 font-semibold text-slate-600 hover:bg-[#4F7CFF]/[0.08] hover:text-slate-950 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-300"
                  >
                    <RotateCcw className="size-3.5" />
                    全部重新生成
                  </button>
                  <button
                    type="button"
                    onClick={() => onDeleteResults(selectedConversation.id, turn.id)}
                    className="studio-button inline-flex size-9 items-center justify-center rounded-2xl text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:text-stone-500 dark:hover:bg-rose-950/30 dark:hover:text-rose-300"
                    aria-label="删除生成结果"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function EmptyCreativeState() {
  return (
    <div className="flex min-h-[520px] items-center justify-center px-4 py-8">
      <div className="studio-card w-full max-w-[900px] overflow-hidden bg-white text-left dark:bg-[#171a21]">
        <div className="grid gap-0 lg:grid-cols-[1fr_320px]">
          <div className="p-7 sm:p-9">
            <div className="flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white shadow-[0_18px_44px_rgba(17,24,39,0.18)] dark:bg-white dark:text-slate-950">
              <Sparkles className="size-5" />
            </div>
            <h1 className="mt-5 text-[30px] font-semibold leading-tight text-slate-950 dark:text-stone-50">
              从一个商品想法开始。
            </h1>
            <p className="mt-3 max-w-[60ch] text-[15px] leading-7 text-slate-600 dark:text-stone-300">
              输入 Prompt、上传参考图或选择商品，AI 会把任务、参考图和生成结果按轮次留在这里。这里不是表单结果页，是你的电商视觉工作台。
            </p>
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              {[
                ["Prompt", "描述画面目标、卖点和平台"],
                ["Reference", "上传商品图并保持主体一致"],
                ["Result", "下载、收藏、继续编辑或重生"],
              ].map(([title, body]) => (
                <div key={title} className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 py-3 dark:border-white/10 dark:bg-white/[0.04]">
                  <p className="text-sm font-semibold text-slate-950 dark:text-stone-50">{title}</p>
                  <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">{body}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="relative min-h-[280px] overflow-hidden border-t border-black/[0.06] bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04] lg:border-l lg:border-t-0">
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(79,124,255,.22),transparent_32%),radial-gradient(circle_at_80%_75%,rgba(109,94,247,.18),transparent_34%)]" />
            <div className="relative grid h-full place-items-center p-8">
              <div className="soft-float w-full max-w-[220px] overflow-hidden rounded-[24px] border border-white/70 bg-white/82 p-3 shadow-[0_24px_70px_rgba(15,23,42,0.16)] backdrop-blur">
                <div className="aspect-[4/5] rounded-[18px] bg-[linear-gradient(145deg,#111827,#4F7CFF_55%,#F8FAFC)]" />
                <div className="mt-3 h-3 w-28 rounded-full bg-slate-200" />
                <div className="mt-2 h-3 w-20 rounded-full bg-slate-100" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function getTurnStatusClass(status: ImageTurnStatus) {
  if (status === "queued") {
    return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-950/30 dark:text-amber-200";
  }
  if (status === "generating") {
    return "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-950/30 dark:text-blue-200";
  }
  if (status === "success") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-200";
  }
  if (status === "canceled") {
    return "border-slate-200 bg-slate-50 text-slate-600 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300";
  }
  return "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-950/30 dark:text-rose-200";
}

function getTurnStatusLabel(status: ImageTurnStatus) {
  if (status === "queued") {
    return "排队中";
  }
  if (status === "generating") {
    return "处理中";
  }
  if (status === "success") {
    return "已完成";
  }
  return "失败";
}

function getQualityCheckLabel(status: NonNullable<StoredImage["qualityCheck"]>["status"]) {
  if (status === "analyzing") {
    return "AI 质检中";
  }
  if (status === "passed") {
    return "质检通过";
  }
  if (status === "failed") {
    return "需重做";
  }
  return "需复核";
}

function getQualityCheckClass(status: NonNullable<StoredImage["qualityCheck"]>["status"]) {
  if (status === "analyzing") {
    return "border-blue-200 bg-white/90 text-blue-700 dark:border-blue-500/30 dark:bg-[#171a21]/90 dark:text-blue-200";
  }
  if (status === "passed") {
    return "border-emerald-200 bg-white/90 text-emerald-700 dark:border-emerald-500/30 dark:bg-[#171a21]/90 dark:text-emerald-200";
  }
  if (status === "failed") {
    return "border-rose-200 bg-white/90 text-rose-700 dark:border-rose-500/30 dark:bg-[#171a21]/90 dark:text-rose-200";
  }
  return "border-amber-200 bg-white/90 text-amber-700 dark:border-amber-500/30 dark:bg-[#171a21]/90 dark:text-amber-200";
}

function formatElapsed(seconds: number): string {
  return `${seconds.toFixed(1)}s`;
}

function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

const base64SizeCache = new Map<string, string>();
function formatBase64ImageSize(base64: string) {
  let cached = base64SizeCache.get(base64);
  if (cached !== undefined) return cached;
  const normalized = base64.replace(/\s/g, "");
  const padding = normalized.endsWith("==") ? 2 : normalized.endsWith("=") ? 1 : 0;
  const bytes = Math.max(0, Math.floor((normalized.length * 3) / 4) - padding);

  if (bytes >= 1024 * 1024) {
    cached = `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  } else if (bytes >= 1024) {
    cached = `${(bytes / 1024).toFixed(1)} KB`;
  } else {
    cached = `${bytes} B`;
  }
  base64SizeCache.set(base64, cached);
  return cached;
}

function formatImageDimensions(width: number, height: number) {
  return `${width} x ${height}`;
}

const LazyImage = memo(function LazyImage({
  src,
  alt,
  className,
  onLoad,
  onOpen,
}: {
  src: string;
  alt: string;
  className: string;
  onLoad?: (event: SyntheticEvent<HTMLImageElement>) => void;
  onOpen?: () => void;
}) {
  const [isVisible, setIsVisible] = useState(false);
  const imgRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = imgRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "400px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={imgRef} className="relative">
      {isVisible ? (
        <button type="button" onClick={onOpen} className={className}>
          <img
            src={src}
            alt={alt}
            className="block h-auto w-full object-cover transition duration-300 group-hover:scale-[1.025]"
            onLoad={onLoad}
          />
        </button>
      ) : (
        <div className={`studio-skeleton min-h-[260px] ${className}`} />
      )}
    </div>
  );
});
