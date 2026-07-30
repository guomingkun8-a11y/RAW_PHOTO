"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Download,
  Heart,
  ImageIcon,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Star,
  Trash2,
  WandSparkles,
} from "lucide-react";
import { toast } from "sonner";

import { ImageThumbnail } from "@/components/image-thumbnail";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  fetchImageLibrary,
  fetchProducts,
  fetchPromptTemplates,
  updateImageLibraryItem,
  type BusinessProduct,
  type ImageLibraryCursor,
  type ImageLibraryItem,
  type PromptTemplate,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 80;

function formatFileSize(bytes?: number) {
  if (!bytes || bytes <= 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatMode(mode: string) {
  return mode === "edit" ? "图生图" : "文生图";
}

function formatDimensions(item: ImageLibraryItem) {
  if (item.width && item.height) return `${item.width} x ${item.height}`;
  return item.size || "";
}

function formatCreatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function readSearchParam() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("search")?.trim() || "";
}

async function downloadImageUrl(src: string, filename: string) {
  const response = await fetch(src);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function aiAnalysis(item: ImageLibraryItem) {
  const prompt = `${item.prompt || item.revised_prompt || ""}`.trim();
  if (prompt.includes("详情") || prompt.toLowerCase().includes("detail")) {
    return "适合详情页首屏，建议继续强化痛点标题、功能分区和信任背书。";
  }
  if (prompt.includes("白底") || item.size === "1024x1024") {
    return "适合作为商品主图或平台首图，主体清晰，建议检查边缘和包装文字。";
  }
  if (prompt.includes("小红书") || prompt.toLowerCase().includes("tiktok")) {
    return "适合社媒封面，建议保留顶部标题空间并输出竖版变体。";
  }
  return "画面可作为商业视觉资产复用，建议根据平台规格继续生成一组同风格变体。";
}

export default function ImageLibraryPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const [items, setItems] = useState<ImageLibraryItem[]>([]);
  const [products, setProducts] = useState<BusinessProduct[]>([]);
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<ImageLibraryCursor | null>(null);
  const [query, setQuery] = useState("");
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<number | null>(null);
  const didInitialLoadRef = useRef(false);
  const requestIdRef = useRef(0);
  const skipNextFilterLoadRef = useRef(false);

  const productMap = useMemo(() => new Map(products.map((item) => [item.id, item])), [products]);
  const templateMap = useMemo(() => new Map(templates.map((item) => [item.id, item])), [templates]);
  const selectedItem = useMemo(
    () => (selectedItemId == null ? null : items.find((item) => item.id === selectedItemId) ?? null),
    [items, selectedItemId],
  );

  const loadImages = useCallback(
    async ({
      reset = true,
      cursor = null,
      search = "",
      productId = 0,
      templateId = 0,
      favorite = false,
    }: {
      reset?: boolean;
      cursor?: ImageLibraryCursor | null;
      search?: string;
      productId?: number;
      templateId?: number;
      favorite?: boolean;
    } = {}) => {
      const requestId = ++requestIdRef.current;
      if (reset) {
        setIsLoading(true);
      } else {
        setIsLoadingMore(true);
      }

      try {
        const data = await fetchImageLibrary({
          limit: PAGE_SIZE,
          cursor: reset ? null : cursor,
          q: search.trim(),
          productId,
          templateId,
          favorite,
        });
        if (requestId !== requestIdRef.current) return;
        setItems((prev) => (reset ? data.items : [...prev, ...data.items]));
        setTotal(data.total);
        setHasMore(data.has_more);
        setNextCursor(data.next_cursor);
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取历史图库失败";
        toast.error(message);
      } finally {
        if (requestId === requestIdRef.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [],
  );

  const currentFilters = useCallback(
    () => ({
      search: query.trim(),
      productId: selectedProductId ?? 0,
      templateId: selectedTemplateId ?? 0,
      favorite: favoriteOnly,
    }),
    [query, selectedProductId, selectedTemplateId, favoriteOnly],
  );

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    const loadFilters = async () => {
      try {
        const [productData, templateData] = await Promise.all([
          fetchProducts({ status: "active" }),
          fetchPromptTemplates(),
        ]);
        if (!cancelled) {
          setProducts(productData.items);
          setTemplates(templateData.items);
        }
      } catch {
        if (!cancelled) {
          setProducts([]);
          setTemplates([]);
        }
      }
    };
    void loadFilters();
    return () => {
      cancelled = true;
    };
  }, [Boolean(session)]);

  useEffect(() => {
    if (isCheckingAuth || !session || didInitialLoadRef.current) return;

    didInitialLoadRef.current = true;
    const initialSearch = readSearchParam();
    if (initialSearch) {
      skipNextFilterLoadRef.current = true;
      setQuery(initialSearch);
    }
    void loadImages({ reset: true, search: initialSearch });
  }, [isCheckingAuth, Boolean(session), loadImages]);

  useEffect(() => {
    if (isCheckingAuth || !session || !didInitialLoadRef.current) return;
    if (skipNextFilterLoadRef.current) {
      skipNextFilterLoadRef.current = false;
      return;
    }
    const timer = window.setTimeout(() => {
      void loadImages({ reset: true, ...currentFilters() });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [isCheckingAuth, Boolean(session), query, selectedProductId, selectedTemplateId, favoriteOnly, loadImages, currentFilters]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleGlobalSearch = (event: Event) => {
      const queryValue = event instanceof CustomEvent ? String(event.detail?.query || "").trim() : "";
      skipNextFilterLoadRef.current = true;
      setQuery(queryValue);
      void loadImages({
        reset: true,
        search: queryValue,
        productId: selectedProductId ?? 0,
        templateId: selectedTemplateId ?? 0,
        favorite: favoriteOnly,
      });
    };
    window.addEventListener("image-library-search", handleGlobalSearch);
    return () => window.removeEventListener("image-library-search", handleGlobalSearch);
  }, [loadImages, selectedProductId, selectedTemplateId, favoriteOnly]);

  const handleDownload = async (item: ImageLibraryItem) => {
    try {
      await downloadImageUrl(item.image_url, `image-${item.id}.png`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "下载图片失败";
      toast.error(message);
    }
  };

  const handleRefresh = () => {
    void loadImages({ reset: true, ...currentFilters() });
  };

  const handleLoadMore = () => {
    if (!nextCursor || isLoadingMore) return;
    void loadImages({ reset: false, cursor: nextCursor, ...currentFilters() });
  };

  const toggleFavorite = async (item: ImageLibraryItem) => {
    try {
      const nextFavorite = !item.favorite;
      await updateImageLibraryItem(item.id, { favorite: nextFavorite });
      setItems((prev) =>
        prev.map((current) => (current.id === item.id ? { ...current, favorite: nextFavorite } : current)),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新收藏失败";
      toast.error(message);
    }
  };

  const deleteImage = async (item: ImageLibraryItem) => {
    try {
      await updateImageLibraryItem(item.id, { deleted: true });
      setItems((prev) => prev.filter((current) => current.id !== item.id));
      setTotal((value) => Math.max(0, value - 1));
      if (selectedItemId === item.id) setSelectedItemId(null);
      toast.success("图片已移出图库");
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除图片失败";
      toast.error(message);
    }
  };

  if (isCheckingAuth || !session) {
    return (
      <div className="grid min-h-[40vh] place-items-center">
        <div className="studio-skeleton size-10 rounded-2xl" />
      </div>
    );
  }

  return (
    <section className="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-5">
        <div className="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
                Asset Gallery
              </div>
              <h1 className="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">历史图库</h1>
              <p className="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">
                共保存 {total} 张生成结果，当前显示 {items.length} 张。收藏、下载、重新生成和 AI 优化都在图片上完成。
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white px-4 text-slate-700 shadow-none"
              onClick={handleRefresh}
              disabled={isLoading}
            >
              {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              刷新
            </Button>
          </div>

          <div className="mt-5 grid gap-2 xl:grid-cols-[minmax(240px,520px)_190px_190px_auto]">
            <div className="relative">
              <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索 Prompt、模型、商品或优化后的提示词"
                className="h-12 rounded-2xl border-black/[0.06] bg-[#F8FAFC] pl-11 text-sm shadow-none focus-visible:ring-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.04]"
              />
            </div>
            <Select
              value={selectedProductId ? String(selectedProductId) : "all"}
              onValueChange={(value) => setSelectedProductId(value === "all" ? null : Number(value))}
            >
              <SelectTrigger className="h-12 rounded-2xl border-black/[0.06] bg-[#F8FAFC] text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="rounded-2xl">
                <SelectItem value="all">全部商品</SelectItem>
                {products.map((product) => (
                  <SelectItem key={product.id} value={String(product.id)}>
                    {product.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={selectedTemplateId ? String(selectedTemplateId) : "all"}
              onValueChange={(value) => setSelectedTemplateId(value === "all" ? null : Number(value))}
            >
              <SelectTrigger className="h-12 rounded-2xl border-black/[0.06] bg-[#F8FAFC] text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="rounded-2xl">
                <SelectItem value="all">全部模板</SelectItem>
                {templates.map((template) => (
                  <SelectItem key={template.id} value={String(template.id)}>
                    {template.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <label className="studio-button inline-flex h-12 w-fit cursor-pointer items-center gap-2 rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 text-sm text-slate-700 hover:bg-[#4F7CFF]/[0.08] dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200">
              <Checkbox
                checked={favoriteOnly}
                onCheckedChange={(checked) => setFavoriteOnly(checked === true)}
                className="size-4 rounded-md"
              />
              只看收藏
            </label>
          </div>
        </div>

        {isLoading && items.length === 0 ? (
          <div className="columns-1 gap-4 space-y-4 sm:columns-2 xl:columns-3 2xl:columns-4">
            {Array.from({ length: 12 }).map((_, index) => (
              <div key={index} className={cn("studio-skeleton break-inside-avoid rounded-[20px]", index % 3 === 0 ? "h-[420px]" : index % 3 === 1 ? "h-[300px]" : "h-[360px]")} />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
            <div>
              <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
                <ImageIcon className="size-5" />
              </div>
              <p className="mt-4 text-[15px] font-semibold text-slate-950 dark:text-stone-50">暂无匹配图片</p>
              <p className="mt-2 text-sm text-slate-500 dark:text-stone-400">生成成功后的图片会自动进入这里。</p>
            </div>
          </div>
        ) : (
          <div className="columns-1 gap-4 space-y-4 sm:columns-2 xl:columns-3 2xl:columns-4">
            {items.map((item) => {
              const product = item.product_id ? productMap.get(item.product_id) : null;
              const template = item.template_id ? templateMap.get(item.template_id) : null;
              const metaLabel = [item.model, formatDimensions(item)].filter(Boolean).join(" / ");
              return (
                <article
                  key={item.id}
                  className="group relative mb-4 break-inside-avoid overflow-hidden rounded-[20px] border border-black/[0.06] bg-white shadow-[0_16px_40px_rgba(15,23,42,0.08)] transition duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-1 hover:border-[#4F7CFF]/25 hover:shadow-[0_24px_70px_rgba(15,23,42,0.14)] dark:border-white/10 dark:bg-[#171a21]"
                >
                  <button
                    type="button"
                    className="block w-full bg-slate-100 text-left dark:bg-[#111317]"
                    onClick={() => setSelectedItemId(item.id)}
                  >
                    <ImageThumbnail
                      src={item.image_url}
                      thumbnailSrc={item.thumbnail_url}
                      alt={item.prompt || "历史图片"}
                      className="w-full"
                      imageClassName="h-auto w-full object-cover transition duration-300 group-hover:scale-[1.03]"
                    />
                  </button>
                  <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-slate-950/82 via-slate-950/10 to-transparent opacity-0 transition duration-300 group-hover:opacity-100" />
                  <div className="absolute inset-x-0 bottom-0 translate-y-4 p-3 opacity-0 transition duration-300 group-hover:translate-y-0 group-hover:opacity-100">
                    <div className="rounded-2xl bg-white/94 p-3 text-[12px] leading-5 text-slate-700 shadow-lg backdrop-blur dark:bg-[#171a21]/94 dark:text-stone-200">
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="rounded-full bg-slate-950 px-2 py-1 text-[11px] font-semibold text-white dark:bg-white dark:text-slate-950">
                          {formatMode(item.mode)}
                        </span>
                        <span className="text-slate-400">{formatCreatedAt(item.created_at)}</span>
                      </div>
                      <p className="line-clamp-2 font-medium text-slate-950 dark:text-stone-50">
                        {item.prompt || item.revised_prompt || "未记录提示词"}
                      </p>
                      <p className="mt-1 truncate text-slate-500 dark:text-stone-400">
                        {[product?.name, template?.name, metaLabel].filter(Boolean).join(" / ") || "未绑定业务对象"}
                      </p>
                    </div>
                    <div className="mt-2 flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void toggleFavorite(item);
                        }}
                        className={cn(
                          "pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-600 shadow-lg transition hover:scale-105",
                          item.favorite && "text-amber-500",
                        )}
                        aria-label={item.favorite ? "取消收藏" : "收藏"}
                      >
                        <Star className={cn("size-4", item.favorite && "fill-current")} />
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleDownload(item);
                        }}
                        className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-600 shadow-lg transition hover:scale-105 hover:text-[#4F7CFF]"
                        aria-label="下载"
                      >
                        <Download className="size-4" />
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void deleteImage(item);
                        }}
                        className="pointer-events-auto inline-flex size-9 items-center justify-center rounded-xl bg-white text-slate-600 shadow-lg transition hover:scale-105 hover:text-rose-600"
                        aria-label="删除"
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {!isLoading && items.length > 0 && hasMore ? (
          <div className="flex justify-center py-6">
            <Button
              type="button"
              variant="outline"
              className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white px-5 text-slate-700 shadow-none"
              onClick={handleLoadMore}
              disabled={isLoadingMore}
            >
              {isLoadingMore ? <LoaderCircle className="size-4 animate-spin" /> : null}
              加载更多
            </Button>
          </div>
        ) : null}
      </div>

      <Sheet open={Boolean(selectedItem)} onOpenChange={(open) => (!open ? setSelectedItemId(null) : null)}>
        <SheetContent side="right" className="w-full overflow-y-auto border-black/[0.06] bg-white p-0 shadow-[0_24px_70px_rgba(15,23,42,0.18)] sm:max-w-[520px] dark:border-white/10 dark:bg-[#171a21]">
          {selectedItem ? (
            <>
              <SheetHeader className="border-b border-black/[0.06] px-5 py-5 text-left dark:border-white/10">
                <SheetTitle className="text-xl">图片详情</SheetTitle>
                <SheetDescription>
                  Prompt、模型、尺寸、生成时间和 AI 分析。
                </SheetDescription>
              </SheetHeader>
              <div className="space-y-5 p-5">
                <div className="overflow-hidden rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] dark:border-white/10 dark:bg-white/[0.04]">
                  <img src={selectedItem.image_url} alt={selectedItem.prompt || "图片详情"} className="h-auto w-full object-contain" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    ["模型", selectedItem.model || "未知"],
                    ["尺寸", formatDimensions(selectedItem) || "未知"],
                    ["质量", selectedItem.quality || "auto"],
                    ["时间", formatCreatedAt(selectedItem.created_at)],
                    ["模式", formatMode(selectedItem.mode)],
                    ["文件", formatFileSize(selectedItem.file_size) || "未知"],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-3 py-3 dark:border-white/10 dark:bg-white/[0.04]">
                      <div className="text-[12px] text-slate-500">{label}</div>
                      <div className="mt-1 truncate text-sm font-semibold text-slate-950 dark:text-stone-50">{value}</div>
                    </div>
                  ))}
                </div>
                <div className="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]">
                  <div className="mb-2 text-[13px] font-semibold text-slate-700 dark:text-stone-300">Prompt</div>
                  <p className="whitespace-pre-wrap text-sm leading-7 text-slate-700 dark:text-stone-200">
                    {selectedItem.prompt || "未记录"}
                  </p>
                </div>
                <div className="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]">
                  <div className="mb-2 text-[13px] font-semibold text-slate-700 dark:text-stone-300">Negative Prompt</div>
                  <p className="text-sm leading-7 text-slate-500 dark:text-stone-400">未设置负向提示词</p>
                </div>
                <div className="rounded-[20px] border border-[#4F7CFF]/20 bg-[#4F7CFF]/10 p-4">
                  <div className="mb-2 inline-flex items-center gap-2 text-[13px] font-semibold text-[#315be8]">
                    <Sparkles className="size-4" />
                    AI 分析
                  </div>
                  <p className="text-sm leading-7 text-slate-700">{aiAnalysis(selectedItem)}</p>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button className="studio-button h-11 rounded-2xl bg-slate-950 text-white hover:bg-slate-800">
                    <RotateCcw className="size-4" />
                    重新生成
                  </Button>
                  <Button variant="outline" className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white shadow-none" onClick={() => void handleDownload(selectedItem)}>
                    <Download className="size-4" />
                    下载
                  </Button>
                  <Button variant="outline" className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white shadow-none" onClick={() => void toggleFavorite(selectedItem)}>
                    <Heart className="size-4" />
                    {selectedItem.favorite ? "取消收藏" : "收藏"}
                  </Button>
                  <Button variant="outline" className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white shadow-none">
                    <WandSparkles className="size-4" />
                    AI 优化
                  </Button>
                </div>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </section>
  );
}
