"use client";

import {
  ArrowUp,
  BadgeCheck,
  Bot,
  Boxes,
  Clock3,
  FolderUp,
  ImagePlus,
  Lightbulb,
  PackageCheck,
  Palette,
  RectangleHorizontal,
  RectangleVertical,
  Replace,
  ShieldCheck,
  Sparkles,
  Square,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";
import {
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type RefObject,
} from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { analyzeImagePrompt, type BusinessProduct, type ImageModel, type PromptTemplate } from "@/lib/api";
import { cn } from "@/lib/utils";

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageRatio: string;
  imageTier: string;
  imageWidth: string;
  imageHeight: string;
  imageQuality: string;
  imageModel: ImageModel;
  imageModels: ImageModel[];
  products: BusinessProduct[];
  promptTemplates: PromptTemplate[];
  selectedProductId: number | null;
  selectedTemplateId: number | null;
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  batchProductImage: { name: string; dataUrl: string } | null;
  batchFolderImages: Array<{ name: string; dataUrl: string }>;
  preserveSubject: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageRatioChange: (value: string) => void;
  onImageTierChange: (value: string) => void;
  onImageWidthChange: (value: string) => void;
  onImageHeightChange: (value: string) => void;
  onImageQualityChange: (value: string) => void;
  onImageModelChange: (value: ImageModel) => void;
  onSelectedProductChange: (value: number | null) => void;
  onSelectedTemplateChange: (value: number | null) => void;
  onPreserveSubjectChange: (value: boolean) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onPickBatchProductImage: () => void | Promise<void>;
  onPickBatchFolder: () => void | Promise<void>;
  onClearBatchReplace: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
};

const templateCards = [
  {
    id: "main",
    label: "电商主图",
    platform: "Tmall",
    prompt: "生成高端电商主图，商品居中，干净背景，突出材质、功能和购买欲，画面真实商业摄影质感。",
    image: "https://picsum.photos/seed/ai-main-product/640/420",
  },
  {
    id: "detail",
    label: "详情页",
    platform: "DTC",
    prompt: "生成商品详情页首屏视觉，包含痛点、核心卖点、使用场景和信任感表达，版式高级清晰。",
    image: "https://picsum.photos/seed/ai-detail-page/640/420",
  },
  {
    id: "scene",
    label: "场景图",
    platform: "Lifestyle",
    prompt: "生成真实生活方式场景图，商品自然融入空间，光线柔和，商业摄影级构图。",
    image: "https://picsum.photos/seed/ai-scene-shot/640/420",
  },
  {
    id: "white",
    label: "白底图",
    platform: "Marketplace",
    prompt: "生成平台通用白底图，商品边缘清晰，比例准确，保持包装文字和主体结构一致。",
    image: "https://picsum.photos/seed/ai-white-bg/640/420",
  },
  {
    id: "xiaohongshu",
    label: "小红书",
    platform: "Social",
    prompt: "生成小红书种草封面，画面有真实使用氛围，标题区域留白，色彩清透高级。",
    image: "https://picsum.photos/seed/ai-redbook-cover/640/420",
  },
  {
    id: "banner",
    label: "Banner",
    platform: "Campaign",
    prompt: "生成横版活动 Banner，商品在视觉中心，适合官网或店铺首页，空间感强，文案区域清晰。",
    image: "https://picsum.photos/seed/ai-commerce-banner/640/420",
  },
];

const imageFileNamePattern = /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i;

function isImageFile(file: File) {
  return file.type.startsWith("image/") || (!file.type && imageFileNamePattern.test(file.name));
}

function hasDraggedImages(dataTransfer: DataTransfer) {
  const items = Array.from(dataTransfer.items || []);
  if (items.length > 0) {
    return items.some((item) => item.kind === "file" && (item.type.startsWith("image/") || !item.type));
  }
  return Array.from(dataTransfer.files || []).some(isImageFile);
}

function getDraggedImageFiles(dataTransfer: DataTransfer) {
  return Array.from(dataTransfer.files || []).filter(isImageFile);
}

const qualityOptions = [
  { value: "auto", label: "自动" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
];

const aspectOptions = [
  { ratio: "1:1", tier: "1k", width: "1024", height: "1024", label: "1:1", icon: Square },
  { ratio: "2:3", tier: "1k", width: "1024", height: "1536", label: "详情页", icon: RectangleVertical },
  { ratio: "3:2", tier: "1k", width: "1536", height: "1024", label: "横图", icon: RectangleHorizontal },
  { ratio: "3:4", tier: "1k", width: "1024", height: "1365", label: "种草", icon: RectangleVertical },
  { ratio: "9:16", tier: "1k", width: "1088", height: "1920", label: "短视频", icon: RectangleVertical },
  { ratio: "16:9", tier: "1k", width: "1920", height: "1088", label: "Banner", icon: RectangleHorizontal },
  { ratio: "1:1", tier: "2k", width: "2048", height: "2048", label: "1:1 2K", icon: Square },
  { ratio: "16:9", tier: "2k", width: "2560", height: "1440", label: "16:9 2K", icon: RectangleHorizontal },
  { ratio: "auto", tier: "auto", width: "1024", height: "1024", label: "Auto", icon: null },
];

const countOptions = ["1", "2", "3", "4", "6", "8"];

function formatModelLabel(model: string) {
  if (model === "gemini-3.1-flash-image-preview") {
    return "Nano Banana 2";
  }
  if (model === "gpt-image-2-guan") {
    return "GPT Image 2 Guan";
  }
  return model;
}

function productCover(product: BusinessProduct) {
  return product.cover_image_url || product.references[0]?.thumbnail_url || product.references[0]?.image_url || "";
}

export function ImageComposer({
  prompt,
  imageCount,
  imageRatio,
  imageTier,
  imageWidth,
  imageHeight,
  imageQuality,
  imageModel,
  imageModels,
  products,
  promptTemplates,
  selectedProductId,
  selectedTemplateId,
  availableQuota,
  activeTaskCount,
  referenceImages,
  batchProductImage,
  batchFolderImages,
  preserveSubject,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageRatioChange,
  onImageTierChange,
  onImageWidthChange,
  onImageHeightChange,
  onImageQualityChange,
  onImageModelChange,
  onSelectedProductChange,
  onSelectedTemplateChange,
  onPreserveSubjectChange,
  onSubmit,
  onPickReferenceImage,
  onPickBatchProductImage,
  onPickBatchFolder,
  onClearBatchReplace,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [selectedVisualTemplate, setSelectedVisualTemplate] = useState(templateCards[0].id);
  const [promptAssistAction, setPromptAssistAction] = useState<"suggest" | "optimize" | "enhance" | null>(null);
  const [promptAssistNote, setPromptAssistNote] = useState("");
  const cardRailRef = useRef<HTMLDivElement>(null);

  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: `${image.name}-${index}`, src: image.dataUrl })),
    [referenceImages],
  );
  const modelOptions = useMemo(
    () => imageModels.map((model) => ({ value: model, label: formatModelLabel(model) })),
    [imageModels],
  );
  const selectedProduct = products.find((item) => item.id === selectedProductId);
  const selectedModelLabel = modelOptions.find((option) => option.value === imageModel)?.label || imageModel;
  const isCodexModel = imageModel.toLowerCase().includes("codex");
  const hasReferenceImages = referenceImages.length > 0;
  const hasBatchReplace = Boolean(batchProductImage || batchFolderImages.length > 0);
  const batchReady = Boolean(batchProductImage && batchFolderImages.length > 0);
  const canPreserveSubject = hasReferenceImages || hasBatchReplace;
  const canSubmit = Boolean(prompt.trim()) || batchReady;
  const qualityLabel = qualityOptions.find((option) => option.value === imageQuality)?.label || "自动";
  const imageSizeLabel = `${imageWidth} x ${imageHeight} / ${qualityLabel} / ${imageCount || 1} 张`;
  const normalizedImageCount = imageCount || "1";
  const isCustomImageCount = !countOptions.includes(normalizedImageCount);
  const composerTitle = "Creative Prompt";
  const composerDescription = "上传、拖拽、粘贴参考图，直接把商品意图交给 AI。";
  const promptPlaceholder = batchReady
    ? "可补充替换要求：例如保留原图阴影、不要改变人物和背景..."
    : hasReferenceImages
      ? "描述你希望如何修改参考图..."
      : "今天想生成什么？";
  const visibleProducts = products.slice(0, 6);

  const handleTextareaPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) {
      return;
    }

    event.preventDefault();
    void onReferenceImageChange(imageFiles);
  };

  const handleComposerDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!hasDraggedImages(event.dataTransfer)) {
      return;
    }

    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDraggingImage(true);
  };

  const handleComposerDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!hasDraggedImages(event.dataTransfer)) {
      return;
    }

    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDraggingImage(true);
  };

  const handleComposerDragLeave = (event: DragEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
      return;
    }
    setIsDraggingImage(false);
  };

  const handleComposerDrop = (event: DragEvent<HTMLDivElement>) => {
    const imageFiles = getDraggedImageFiles(event.dataTransfer);
    if (event.dataTransfer.files.length > 0 || imageFiles.length > 0) {
      event.preventDefault();
      event.stopPropagation();
    }

    setIsDraggingImage(false);
    if (imageFiles.length === 0) {
      return;
    }

    void onReferenceImageChange(imageFiles);
  };

  const applyTemplateCard = (card: (typeof templateCards)[number]) => {
    setSelectedVisualTemplate(card.id);
    onSelectedTemplateChange(null);
    if (!prompt.trim()) {
      onPromptChange(card.prompt);
    }
    cardRailRef.current?.scrollTo({ left: 0, behavior: "smooth" });
  };

  const analyzeReferenceForPrompt = async (action: "suggest" | "optimize" | "enhance") => {
    if (!hasReferenceImages || promptAssistAction) {
      setPromptAssistNote("请先上传商品参考图，再让 AI 分析图片并生成 Prompt。");
      return;
    }

    setPromptAssistAction(action);
    setPromptAssistNote("AI 正在调用视觉模型分析参考图...");
    try {
      const result = await analyzeImagePrompt({
        action,
        mode: "single",
        prompt: prompt.trim(),
        images: referenceImages.slice(0, 4).map((image) => ({ name: image.name, dataUrl: image.dataUrl })),
        product: selectedProduct
          ? {
              name: selectedProduct.name,
              sku: selectedProduct.sku,
              brand: selectedProduct.brand,
              category: selectedProduct.category,
              sellingPoints: selectedProduct.selling_points,
            }
          : undefined,
      });

      const analysisLines = [
        result.analysis.subject ? `主体识别：${result.analysis.subject}` : "",
        result.analysis.materials ? `材质/细节：${result.analysis.materials}` : "",
        result.analysis.style ? `风格判断：${result.analysis.style}` : "",
        result.analysis.composition ? `构图光线：${result.analysis.composition}` : "",
        result.analysis.textLogo ? `文字/Logo：${result.analysis.textLogo}` : "",
        result.analysis.risks ? `风险提醒：${result.analysis.risks}` : "",
      ].filter(Boolean);
      const promptBody =
        action === "suggest"
          ? [
              "AI 图片分析：",
              ...analysisLines,
              "",
              "Prompt 建议：",
              ...(result.suggestions.length > 0 ? result.suggestions.map((item, index) => `${index + 1}. ${item}`) : [result.suggestionPrompt]),
              "",
              "可直接使用：",
              result.suggestionPrompt,
            ].join("\n")
          : [
              "AI 图片分析：",
              ...analysisLines,
              "",
              "优化后的 Prompt：",
              result.optimizedPrompt,
              result.negativePrompt ? `\nNegative Prompt：${result.negativePrompt}` : "",
            ].join("\n");

      onPromptChange(promptBody.trim());
      setPromptAssistNote(action === "suggest" ? "视觉模型已完成图片分析，并生成 Prompt 建议。" : action === "optimize" ? "视觉模型已完成图片分析，并优化当前 Prompt。" : "视觉模型已完成图片分析，并自动润色 Prompt。");
      textareaRef.current?.focus();
    } catch (error) {
      const message = error instanceof Error ? error.message : "图片分析失败";
      setPromptAssistNote(`图片分析失败：${message}`);
    } finally {
      setPromptAssistAction(null);
    }
  };

  const settingsPanel = (
    <section className="border-b border-black/[0.06] bg-[#F8FAFC] px-4 py-4 dark:border-white/10 dark:bg-white/[0.035] sm:px-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#9db3ff]">
            <Palette className="size-5" />
          </span>
          <div className="min-w-0">
            <h3 className="text-[16px] font-semibold text-slate-950 dark:text-stone-50">模型与画布设置</h3>
            <div className="mt-1 flex flex-wrap gap-1.5 text-[12px] font-medium text-slate-500 dark:text-stone-400">
              <span className="max-w-[260px] truncate rounded-full bg-white px-2 py-1 ring-1 ring-black/[0.06] dark:bg-[#171a21] dark:ring-white/10">
                {selectedModelLabel}
              </span>
              <span className="rounded-full bg-white px-2 py-1 ring-1 ring-black/[0.06] dark:bg-[#171a21] dark:ring-white/10">
                {imageWidth} x {imageHeight}
              </span>
              <span className="rounded-full bg-white px-2 py-1 ring-1 ring-black/[0.06] dark:bg-[#171a21] dark:ring-white/10">
                {qualityLabel} / {imageCount || 1} 张
              </span>
            </div>
          </div>
        </div>
        <span className="rounded-full bg-[#4F7CFF]/10 px-3 py-1.5 text-[12px] font-semibold text-[#315be8] dark:text-[#9db3ff]">
          当前生成配置
        </span>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(240px,320px)_minmax(0,1fr)_minmax(190px,240px)]">
        <div>
          <label className="mb-2 block text-[13px] font-semibold text-slate-700 dark:text-stone-300">模型</label>
          <Select value={imageModel} onValueChange={(value) => onImageModelChange(value as ImageModel)}>
            <SelectTrigger className="h-11 rounded-2xl border-black/[0.06] bg-white text-sm shadow-none dark:border-white/10 dark:bg-[#171a21]">
              <SelectValue placeholder="选择模型" />
            </SelectTrigger>
            <SelectContent className="z-[120] rounded-2xl">
              {modelOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="mt-3 grid grid-cols-4 gap-2">
            {qualityOptions.map((option) => {
              const active = option.value === imageQuality;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    "studio-button h-9 rounded-xl border text-[13px] font-medium",
                    active
                      ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]"
                      : "border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04]",
                  )}
                  onClick={() => onImageQualityChange(option.value)}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label className="mb-2 block text-[13px] font-semibold text-slate-700 dark:text-stone-300">画布尺寸</label>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
            {aspectOptions.map((option) => {
              const active = option.ratio === imageRatio && option.tier === imageTier && option.width === imageWidth && option.height === imageHeight;
              const Icon = option.icon;
              const disabled = !isCodexModel && (option.tier === "2k" || option.tier === "4k");
              return (
                <button
                  key={`${option.ratio}-${option.tier}-${option.label}`}
                  type="button"
                  disabled={disabled}
                  className={cn(
                    "studio-button flex h-[66px] flex-col items-center justify-center gap-1 rounded-2xl border bg-white text-[13px] font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-35 dark:border-white/10 dark:bg-[#171a21]",
                    active ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]" : "border-black/[0.06] hover:bg-[#4F7CFF]/[0.08]",
                  )}
                  onClick={() => {
                    if (disabled) return;
                    onImageRatioChange(option.ratio);
                    onImageTierChange(option.tier);
                    onImageWidthChange(option.width);
                    onImageHeightChange(option.height);
                  }}
                >
                  {Icon ? <Icon className="size-4" /> : <Zap className="size-4" />}
                  <span>{option.label}</span>
                </button>
              );
            })}
          </div>
          <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
            <Input
              type="number"
              inputMode="numeric"
              min="1"
              value={imageWidth}
              onChange={(event) => onImageWidthChange(event.target.value)}
              className="h-10 rounded-2xl border-black/[0.06] bg-white text-center text-sm font-semibold shadow-none dark:border-white/10 dark:bg-[#171a21]"
            />
            <span className="text-slate-400">x</span>
            <Input
              type="number"
              inputMode="numeric"
              min="1"
              value={imageHeight}
              onChange={(event) => onImageHeightChange(event.target.value)}
              className="h-10 rounded-2xl border-black/[0.06] bg-white text-center text-sm font-semibold shadow-none dark:border-white/10 dark:bg-[#171a21]"
            />
          </div>
        </div>

        <div>
          <label className="mb-2 block text-[13px] font-semibold text-slate-700 dark:text-stone-300">数量与状态</label>
          <div className="grid grid-cols-3 gap-2">
            {countOptions.map((option) => {
              const active = normalizedImageCount === option;
              return (
                <button
                  key={option}
                  type="button"
                  className={cn(
                    "studio-button h-10 rounded-2xl border text-[13px] font-semibold",
                    active
                      ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]"
                      : "border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-[#171a21]",
                  )}
                  onClick={() => onImageCountChange(option)}
                >
                  {option} 张
                </button>
              );
            })}
          </div>
          <label className="mt-3 block text-[12px] font-medium text-slate-500 dark:text-stone-400" htmlFor="image-count-custom">
            自定义张数
          </label>
          <div className="mt-1.5 grid grid-cols-[1fr_auto] items-center gap-2">
            <Input
              id="image-count-custom"
              type="number"
              inputMode="numeric"
              min="1"
              max="100"
              value={imageCount}
              placeholder="1-100"
              onChange={(event) => onImageCountChange(event.target.value)}
              className={cn(
                "h-10 rounded-2xl border-black/[0.06] bg-white text-center text-sm font-semibold shadow-none dark:border-white/10 dark:bg-[#171a21]",
                isCustomImageCount && "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]",
              )}
            />
            <span className="rounded-full bg-white px-2.5 py-1.5 text-[12px] font-semibold text-slate-500 ring-1 ring-black/[0.06] dark:bg-[#171a21] dark:ring-white/10">
              张
            </span>
          </div>
          <div className="mt-3 grid gap-2">
            <div className="rounded-2xl border border-black/[0.06] bg-white px-3 py-2 text-[13px] dark:border-white/10 dark:bg-[#171a21]">
              <span className="text-slate-500">剩余额度</span>
              <span className="float-right font-semibold text-slate-950 dark:text-stone-50">{availableQuota}</span>
            </div>
            <div className="rounded-2xl border border-black/[0.06] bg-white px-3 py-2 text-[13px] dark:border-white/10 dark:bg-[#171a21]">
              <span className="text-slate-500">进行中</span>
              <span className="float-right font-semibold text-slate-950 dark:text-stone-50">
                {activeTaskCount > 0 ? `${activeTaskCount} 个任务` : "空闲"}
              </span>
            </div>
            <div className="rounded-2xl border border-black/[0.06] bg-white px-3 py-2 text-[13px] dark:border-white/10 dark:bg-[#171a21]">
              <span className="inline-flex items-center gap-1 text-slate-500">
                <Clock3 className="size-3.5" />
                GPU 状态
              </span>
              <span className="float-right font-semibold text-[#16C784]">正常</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );

  return (
    <div className="studio-card shrink-0 overflow-hidden bg-white dark:bg-[#171a21]">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(event) => {
          void onReferenceImageChange(Array.from(event.target.files || []));
        }}
      />

      <div className="border-b border-black/[0.06] px-4 py-4 dark:border-white/10 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">{composerTitle}</h2>
            <p className="mt-1 text-[13px] text-slate-500 dark:text-stone-400">
              {composerDescription}
            </p>
          </div>
          <div className="inline-flex h-10 items-center gap-2 rounded-2xl border border-black/[0.06] bg-slate-50 px-4 text-[13px] font-semibold text-slate-700 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200">
            <WandSparkles className="size-4 text-[#4F7CFF]" />
            生成图片
          </div>
        </div>
      </div>

      {settingsPanel}

      <div className="grid gap-5 p-4 sm:p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <div
            className={cn(
              "relative overflow-hidden rounded-[20px] border bg-[#F8FAFC] transition-[border-color,box-shadow,background-color] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] dark:bg-white/[0.04]",
              isFocused || prompt.trim()
                ? "border-[#4F7CFF]/45 shadow-[0_0_0_4px_rgba(79,124,255,0.1),0_22px_60px_rgba(79,124,255,0.12)]"
                : "border-black/[0.06] dark:border-white/10",
              isDraggingImage && "border-[#4F7CFF] bg-[#4F7CFF]/[0.08]",
            )}
            onDragEnter={handleComposerDragEnter}
            onDragOver={handleComposerDragOver}
            onDragLeave={handleComposerDragLeave}
            onDrop={handleComposerDrop}
            onClick={() => textareaRef.current?.focus()}
          >
            <ImageLightbox
              images={lightboxImages}
              currentIndex={lightboxIndex}
              open={lightboxOpen}
              onOpenChange={setLightboxOpen}
              onIndexChange={setLightboxIndex}
            />
            <div className="flex items-center justify-between gap-3 border-b border-black/[0.06] px-4 py-3 dark:border-white/10">
              <div className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-slate-600 dark:text-stone-300">
                <span className="relative flex size-8 items-center justify-center rounded-xl bg-[#4F7CFF]/10 text-[#4F7CFF]">
                  <Sparkles className={cn("size-4", (isFocused || prompt.trim()) && "ai-orbit")} />
                </span>
                <span className="truncate">Prompt Studio</span>
              </div>
              <div className="hidden items-center gap-2 text-[12px] text-slate-500 sm:flex">
                <span>{imageSizeLabel}</span>
                <span className="size-1 rounded-full bg-slate-300" />
                <span>{selectedModelLabel}</span>
              </div>
            </div>

            <textarea
              ref={textareaRef}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onPaste={handleTextareaPaste}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder={promptPlaceholder}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void onSubmit();
                }
              }}
              className="min-h-[260px] w-full resize-none bg-transparent px-5 py-5 text-[18px] leading-8 text-slate-950 outline-none placeholder:text-slate-500 dark:text-stone-50 dark:placeholder:text-stone-500 sm:min-h-[320px]"
            />

            {referenceImages.length > 0 ? (
              <div className="flex gap-2 overflow-x-auto px-5 pb-4">
                {referenceImages.map((image, index) => (
                  <div key={`${image.name}-${index}`} className="group relative size-16 shrink-0">
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setLightboxIndex(index);
                        setLightboxOpen(true);
                      }}
                      className="size-16 overflow-hidden rounded-2xl border border-black/[0.06] bg-white shadow-sm"
                      aria-label={`预览参考图 ${image.name || index + 1}`}
                    >
                      <img src={image.dataUrl} alt={image.name || `参考图 ${index + 1}`} className="h-full w-full object-cover" />
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onRemoveReferenceImage(index);
                      }}
                      className="absolute -right-1.5 -top-1.5 inline-flex size-6 items-center justify-center rounded-full bg-slate-950 text-white shadow-sm opacity-0 transition group-hover:opacity-100"
                      aria-label={`移除参考图 ${image.name || index + 1}`}
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}

            {hasBatchReplace ? (
              <div className="mx-5 mb-4 rounded-[18px] border border-[#4F7CFF]/20 bg-white p-3 shadow-sm dark:border-[#4F7CFF]/25 dark:bg-[#171a21]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-[#4F7CFF]/10 text-[#315be8]">
                      <Replace className="size-5" />
                    </span>
                    <div className="min-w-0">
                      <div className="text-[14px] font-semibold text-slate-950 dark:text-stone-50">批量替换商品</div>
                      <p className="mt-0.5 truncate text-[12px] text-slate-500 dark:text-stone-400">
                        {batchReady
                          ? `已就绪：1 张主图将逐张替换 ${batchFolderImages.length} 张文件夹图片中的商品`
                          : "先上传文件夹，再上传要替换进去的商品主图"}
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={onClearBatchReplace}
                    className="studio-button h-9 rounded-xl border border-black/[0.06] bg-white px-3 text-[12px] font-semibold text-slate-600 hover:bg-slate-50 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200"
                  >
                    清空
                  </button>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,180px)_1fr]">
                  <div className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] p-2 dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-slate-600 dark:text-stone-300">
                      <PackageCheck className="size-3.5 text-[#4F7CFF]" />
                      主图商品
                    </div>
                    {batchProductImage ? (
                      <div className="flex items-center gap-2">
                        <img src={batchProductImage.dataUrl} alt={batchProductImage.name || "主图"} className="size-12 rounded-xl object-cover" />
                        <span className="min-w-0 truncate text-[12px] text-slate-500 dark:text-stone-400">{batchProductImage.name}</span>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void onPickBatchProductImage()}
                        className="studio-button flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[#4F7CFF]/30 bg-white text-[12px] font-semibold text-[#315be8] hover:bg-[#4F7CFF]/[0.08] dark:bg-white/[0.04]"
                      >
                        <PackageCheck className="size-4" />
                        上传主图
                      </button>
                    )}
                  </div>
                  <div className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] p-2 dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-slate-600 dark:text-stone-300">
                        <FolderUp className="size-3.5 text-[#4F7CFF]" />
                        文件夹图片
                      </span>
                      <span className="text-[12px] text-slate-500">{batchFolderImages.length} 张</span>
                    </div>
                    {batchFolderImages.length > 0 ? (
                      <div className="flex gap-1.5 overflow-hidden">
                        {batchFolderImages.slice(0, 8).map((image, index) => (
                          <img
                            key={`${image.name}-${index}`}
                            src={image.dataUrl}
                            alt={image.name || `文件夹图片 ${index + 1}`}
                            className="size-11 shrink-0 rounded-xl object-cover"
                          />
                        ))}
                        {batchFolderImages.length > 8 ? (
                          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-[12px] font-semibold text-white">
                            +{batchFolderImages.length - 8}
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void onPickBatchFolder()}
                        className="studio-button flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[#4F7CFF]/30 bg-white text-[12px] font-semibold text-[#315be8] hover:bg-[#4F7CFF]/[0.08] dark:bg-white/[0.04]"
                      >
                        <FolderUp className="size-4" />
                        上传文件夹
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ) : null}

            {isDraggingImage ? (
              <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-[20px] border-2 border-dashed border-[#4F7CFF] bg-white/90 text-sm font-semibold text-slate-950 backdrop-blur-sm dark:bg-[#171a21]/90 dark:text-white">
                <div className="flex items-center gap-3 rounded-2xl bg-slate-950 px-5 py-3 text-white shadow-[0_18px_44px_rgba(79,124,255,0.22)]">
                  <ImagePlus className="size-5" />
                  松开以上传参考图
                </div>
              </div>
            ) : null}

            <div className="border-t border-black/[0.06] bg-white px-4 py-3 dark:border-white/10 dark:bg-white/[0.035]" onClick={(event) => event.stopPropagation()}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="hide-scrollbar flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
                  <Button
                    type="button"
                    variant="outline"
                    className="studio-button h-10 shrink-0 rounded-2xl border-black/[0.06] bg-white text-[13px] text-slate-700 shadow-none hover:bg-[#4F7CFF]/[0.08]"
                    onClick={onPickReferenceImage}
                  >
                    <ImagePlus className="size-4" />
                    上传图片
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className={cn(
                      "studio-button h-10 shrink-0 rounded-2xl border-black/[0.06] bg-white text-[13px] text-slate-700 shadow-none hover:bg-[#4F7CFF]/[0.08]",
                      batchFolderImages.length > 0 && "border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8]",
                    )}
                    onClick={() => void onPickBatchFolder()}
                  >
                    <FolderUp className="size-4" />
                    上传文件夹
                    {batchFolderImages.length > 0 ? (
                      <span className="rounded-full bg-[#4F7CFF]/10 px-1.5 py-0.5 text-[11px] font-semibold text-[#315be8]">
                        {batchFolderImages.length}
                      </span>
                    ) : null}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className={cn(
                      "studio-button h-10 shrink-0 rounded-2xl border-black/[0.06] bg-white text-[13px] text-slate-700 shadow-none hover:bg-[#4F7CFF]/[0.08]",
                      batchProductImage && "border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8]",
                    )}
                    onClick={() => void onPickBatchProductImage()}
                  >
                    <PackageCheck className="size-4" />
                    上传主图
                  </Button>
                  <button
                    type="button"
                    className="studio-button inline-flex h-10 shrink-0 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200"
                    onClick={() => void analyzeReferenceForPrompt("suggest")}
                    disabled={!hasReferenceImages || promptAssistAction !== null}
                    title={hasReferenceImages ? "分析参考图并生成 Prompt 建议" : "上传参考图后可用"}
                  >
                    <Lightbulb className={cn("size-4", promptAssistAction === "suggest" && "ai-orbit")} />
                    {promptAssistAction === "suggest" ? "分析中..." : "Prompt 建议"}
                  </button>
                  <button
                    type="button"
                    className="studio-button inline-flex h-10 shrink-0 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 hover:bg-[#4F7CFF]/[0.08] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200"
                    onClick={() => void analyzeReferenceForPrompt("optimize")}
                    disabled={!hasReferenceImages || promptAssistAction !== null}
                    title={hasReferenceImages ? "分析参考图并优化当前 Prompt" : "上传参考图后可用"}
                  >
                    <WandSparkles className={cn("size-4", promptAssistAction === "optimize" && "ai-orbit")} />
                    {promptAssistAction === "optimize" ? "分析中..." : "Prompt 优化"}
                  </button>
                  <button
                    type="button"
                    className="studio-button inline-flex h-10 shrink-0 items-center gap-2 rounded-2xl border border-[#4F7CFF]/20 bg-[#4F7CFF]/10 px-3 text-[13px] font-semibold text-[#315be8] hover:bg-[#4F7CFF]/15 disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#9db3ff]"
                    onClick={() => void analyzeReferenceForPrompt("enhance")}
                    disabled={!hasReferenceImages || promptAssistAction !== null}
                    title={hasReferenceImages ? "分析参考图并自动润色 Prompt" : "上传参考图后可用"}
                  >
                    <Bot className={cn("size-4", promptAssistAction === "enhance" && "ai-orbit")} />
                    {promptAssistAction === "enhance" ? "分析中..." : "AI 自动润色"}
                  </button>
                  <div
                    className={cn(
                      "inline-flex h-10 shrink-0 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-3 text-[13px] font-medium text-slate-700 transition dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200",
                      canPreserveSubject ? "cursor-pointer hover:bg-[#4F7CFF]/[0.08]" : "cursor-not-allowed opacity-55",
                      canPreserveSubject && preserveSubject && "border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8]",
                    )}
                    title={canPreserveSubject ? "保留第一张参考图中的商品主体、Logo 和文字" : "上传参考图后可开启主体保真"}
                  >
                    <Checkbox
                      id="image-preserve-subject"
                      checked={canPreserveSubject && preserveSubject}
                      disabled={!canPreserveSubject}
                      onCheckedChange={(checked) => {
                        if (canPreserveSubject) {
                          onPreserveSubjectChange(checked === true);
                        }
                      }}
                      className="size-4 rounded-md shadow-none"
                    />
                    <label htmlFor="image-preserve-subject" className={cn("inline-flex items-center gap-2", canPreserveSubject ? "cursor-pointer" : "cursor-not-allowed")}>
                      <ShieldCheck className="size-4" />
                      主体保真
                    </label>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => void onSubmit()}
                  disabled={!canSubmit}
                  className="studio-button inline-flex h-12 shrink-0 items-center gap-2 rounded-2xl bg-slate-950 px-5 text-[15px] font-semibold text-white shadow-[0_18px_44px_rgba(17,24,39,0.18)] hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none dark:bg-white dark:text-slate-950 dark:hover:bg-stone-100 dark:disabled:bg-stone-700 dark:disabled:text-stone-400"
                  aria-label={batchReady ? "批量替换商品" : referenceImages.length > 0 ? "编辑图片" : "生成图片"}
                >
                  <ArrowUp className="size-4" />
                  {batchReady ? "批量替换商品" : referenceImages.length > 0 ? "编辑图片" : "生成图片"}
                </button>
              </div>

              {(isFocused || prompt.trim() || hasReferenceImages || promptAssistNote) ? (
                <div className="mt-3 flex items-center gap-2 text-[13px] font-medium text-[#4F7CFF]">
                  <span className="flex size-5 items-center justify-center rounded-full bg-[#4F7CFF]/10">
                    <Sparkles className={cn("size-3", promptAssistAction && "ai-orbit")} />
                  </span>
                  {promptAssistNote || (batchReady ? "批量替换将逐张提交：每个任务使用主图 + 1 张文件夹图片。" : hasReferenceImages ? "已上传参考图，可点击 Prompt 建议或 Prompt 优化进行图片分析。" : "上传参考图后，AI 才会进行图片分析和 Prompt 优化。")}
                  <span className="h-1 w-24 rounded-full bg-[#4F7CFF]/20">
                    <span className={cn("block h-full rounded-full bg-[#4F7CFF]", promptAssistAction && "ai-pulse-line")} />
                  </span>
                </div>
              ) : null}
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {[
              ["主体识别", batchReady ? "主图会作为固定商品主体，逐张替换文件夹图片中的原商品。" : hasReferenceImages ? "分析商品轮廓、Logo、包装文字与关键结构。" : "上传参考图后启用。"],
              ["风格判断", batchReady ? "保留每张原图的场景、光线、构图和风格。" : hasReferenceImages ? "判断画面构图、光线、材质和商业氛围。" : "不会使用固定模板直接填充。"],
              ["Prompt 输出", batchReady ? "用户补充要求会追加到批量替换基础 Prompt 后。" : hasReferenceImages ? "生成可编辑的建议、优化版 Prompt 和负向约束。" : "点击前请先上传商品图。"],
            ].map(([title, description]) => (
              <div
                key={title}
                className="rounded-2xl border border-black/[0.06] bg-white p-3 text-left dark:border-white/10 dark:bg-white/[0.04]"
              >
                <div className="text-[13px] font-semibold text-slate-950 dark:text-stone-50">{title}</div>
                <p className="mt-1 text-[12px] leading-5 text-slate-500 dark:text-stone-400">{description}</p>
              </div>
            ))}
          </div>
        </div>

        <aside className="min-w-0 space-y-4">
          <section className="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-[15px] font-semibold text-slate-950 dark:text-stone-50">快捷模板</h3>
              <span className="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[11px] font-semibold text-[#315be8]">
                {templateCards.length} presets
              </span>
            </div>
            <div ref={cardRailRef} className="hide-scrollbar grid max-h-[480px] gap-3 overflow-y-auto pr-1">
              {templateCards.map((card) => {
                const active = selectedVisualTemplate === card.id && !selectedTemplateId;
                return (
                  <button
                    key={card.id}
                    type="button"
                    onClick={() => applyTemplateCard(card)}
                    className={cn(
                      "group overflow-hidden rounded-2xl border bg-white text-left shadow-sm transition-[transform,box-shadow,border-color] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:scale-[1.02] hover:shadow-[0_18px_44px_rgba(15,23,42,0.1)] dark:bg-[#171a21]",
                      active ? "border-[#4F7CFF] ring-4 ring-[#4F7CFF]/10" : "border-black/[0.06] dark:border-white/10",
                    )}
                  >
                    <div className="h-24 overflow-hidden bg-slate-100">
                      <img src={card.image} alt="" className="h-full w-full object-cover transition duration-300 group-hover:scale-105" />
                    </div>
                    <div className="p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[14px] font-semibold text-slate-950 dark:text-stone-50">{card.label}</span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500 dark:bg-white/[0.08]">
                          {card.platform}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-slate-500 dark:text-stone-400">
                        {card.prompt}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-3 dark:border-white/10 dark:bg-white/[0.04]">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-[15px] font-semibold text-slate-950 dark:text-stone-50">商品绑定</h3>
              <span className="text-[12px] text-slate-500">{selectedProduct ? "已绑定" : "可选"}</span>
            </div>
            <div className="grid gap-2">
              <button
                type="button"
                onClick={() => onSelectedProductChange(null)}
                className={cn(
                  "studio-button flex items-center gap-3 rounded-2xl border p-3 text-left",
                  selectedProductId == null
                    ? "border-[#4F7CFF]/30 bg-[#4F7CFF]/10 text-[#315be8]"
                    : "border-black/[0.06] bg-white text-slate-600 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04]",
                )}
              >
                <span className="flex size-11 items-center justify-center rounded-xl bg-slate-950 text-white">
                  <Boxes className="size-5" />
                </span>
                <span>
                  <span className="block text-[14px] font-semibold">不绑定商品</span>
                  <span className="mt-0.5 block text-[12px] text-slate-500">生成结果进入通用图库</span>
                </span>
              </button>
              {visibleProducts.map((product) => {
                const cover = productCover(product);
                const active = selectedProductId === product.id;
                return (
                  <button
                    key={product.id}
                    type="button"
                    onClick={() => onSelectedProductChange(product.id)}
                    className={cn(
                      "studio-button group flex items-center gap-3 rounded-2xl border p-3 text-left",
                      active
                        ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-[#315be8]"
                        : "border-black/[0.06] bg-white text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04]",
                    )}
                  >
                    <span className="size-12 shrink-0 overflow-hidden rounded-xl bg-slate-100">
                      {cover ? (
                        <img src={cover} alt="" className="h-full w-full object-cover transition group-hover:scale-105" />
                      ) : (
                        <span className="flex h-full w-full items-center justify-center bg-gradient-to-br from-slate-100 to-[#4F7CFF]/12 text-slate-400">
                          <ImagePlus className="size-5" />
                        </span>
                      )}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-[14px] font-semibold text-slate-950 dark:text-stone-50">
                        {product.name}
                      </span>
                      <span className="mt-0.5 block truncate text-[12px] text-slate-500">
                        {[product.sku, product.brand || product.category].filter(Boolean).join(" / ") || "暂无 SKU"}
                      </span>
                    </span>
                    {active ? <BadgeCheck className="ml-auto size-4 shrink-0 text-[#4F7CFF]" /> : null}
                  </button>
                );
              })}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
