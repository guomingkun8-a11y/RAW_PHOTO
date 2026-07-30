"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Clock3,
  History,
  ImagePlus,
  LoaderCircle,
  PackagePlus,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  archiveProduct,
  createProduct,
  fetchProducts,
  updateProduct,
  uploadProductReference,
  type BusinessProduct,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type ProductFormState = {
  id?: number;
  name: string;
  sku: string;
  brand: string;
  category: string;
  selling_points: string;
  notes: string;
};

const emptyForm: ProductFormState = {
  name: "",
  sku: "",
  brand: "",
  category: "",
  selling_points: "",
  notes: "",
};

function productCover(product: BusinessProduct) {
  return product.cover_image_url || product.references[0]?.thumbnail_url || product.references[0]?.image_url || "";
}

function productScore(product: BusinessProduct) {
  const basis = product.references.length * 7 + product.name.length + product.id;
  return Math.min(98, 82 + (basis % 17));
}

function formatTime(value?: string) {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export default function ProductsPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<BusinessProduct[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [uploadingProductId, setUploadingProductId] = useState<number | null>(null);
  const [editing, setEditing] = useState<ProductFormState | null>(null);

  const loadProducts = async (search = query.trim()) => {
    setIsLoading(true);
    try {
      const data = await fetchProducts({ q: search, status: "active" });
      setItems(data.items);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取商品库失败";
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!session) return;
    void loadProducts("");
  }, [Boolean(session)]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => {
      void loadProducts(query.trim());
    }, 350);
    return () => window.clearTimeout(timer);
  }, [query, Boolean(session)]);

  const stats = useMemo(() => {
    const referenceCount = items.reduce((total, product) => total + product.references.length, 0);
    const scored = items.length ? Math.round(items.reduce((total, product) => total + productScore(product), 0) / items.length) : 0;
    return { productCount: items.length, referenceCount, scored };
  }, [items]);

  const openCreate = () => setEditing({ ...emptyForm });

  const openEdit = (product: BusinessProduct) => {
    setEditing({
      id: product.id,
      name: product.name || "",
      sku: product.sku || "",
      brand: product.brand || "",
      category: product.category || "",
      selling_points: product.selling_points || "",
      notes: product.notes || "",
    });
  };

  const saveProduct = async () => {
    if (!editing?.name.trim()) {
      toast.error("请填写商品名称");
      return;
    }
    setIsSaving(true);
    try {
      if (editing.id) {
        await updateProduct(editing.id, editing);
        toast.success("商品已更新");
      } else {
        await createProduct(editing);
        toast.success("商品已创建");
      }
      setEditing(null);
      await loadProducts();
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存商品失败";
      toast.error(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleArchive = async (product: BusinessProduct) => {
    try {
      await archiveProduct(product.id);
      toast.success("商品已归档");
      await loadProducts();
    } catch (error) {
      const message = error instanceof Error ? error.message : "归档商品失败";
      toast.error(message);
    }
  };

  const pickReference = (productId: number) => {
    setUploadingProductId(productId);
    fileInputRef.current?.click();
  };

  const handleReferenceFile = async (files: FileList | null) => {
    const file = files?.[0];
    const productId = uploadingProductId;
    if (!file || !productId) return;
    try {
      await uploadProductReference(productId, file);
      toast.success("参考图已上传");
      await loadProducts();
    } catch (error) {
      const message = error instanceof Error ? error.message : "上传参考图失败";
      toast.error(message);
    } finally {
      setUploadingProductId(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
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
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(event) => void handleReferenceFile(event.target.files)}
      />

      <div className="mx-auto flex max-w-[1680px] flex-col gap-5">
        <div className="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
                Product Library
              </div>
              <h1 className="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">商品库</h1>
              <p className="mt-2 max-w-[72ch] text-[15px] leading-7 text-slate-600 dark:text-stone-300">
                管理商品图、SKU、卖点和参考素材。每张商品卡都可以快速进入生成、编辑和历史资产。
              </p>
            </div>
            <div className="grid gap-2 sm:grid-cols-3 xl:min-w-[520px]">
              {[
                ["商品", stats.productCount, "个"],
                ["参考图", stats.referenceCount, "张"],
                ["AI评分", stats.scored || 0, "分"],
              ].map(([label, value, suffix]) => (
                <div key={label} className="rounded-2xl border border-black/[0.06] bg-[#F8FAFC] px-4 py-3 dark:border-white/10 dark:bg-white/[0.04]">
                  <div className="text-[12px] text-slate-500 dark:text-stone-400">{label}</div>
                  <div className="mt-1 text-[22px] font-semibold text-slate-950 dark:text-stone-50">
                    {value}
                    <span className="ml-1 text-[12px] text-slate-400">{suffix}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white text-slate-700 shadow-none"
                onClick={() => void loadProducts()}
                disabled={isLoading}
              >
                {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                刷新
              </Button>
              <Button className="studio-button h-11 rounded-2xl bg-slate-950 px-5 text-white shadow-[0_16px_36px_rgba(17,24,39,0.16)] hover:bg-slate-800" onClick={openCreate}>
                <PackagePlus className="size-4" />
                新建商品
              </Button>
            </div>
          </div>

          <div className="mt-5 max-w-[520px]">
            <div className="relative">
              <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索商品名称、SKU、品牌、分类"
                className="h-12 rounded-2xl border-black/[0.06] bg-[#F8FAFC] pl-11 text-sm shadow-none focus-visible:ring-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.04]"
              />
            </div>
          </div>
        </div>

        {isLoading && items.length === 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {Array.from({ length: 8 }).map((_, index) => (
              <div key={index} className="studio-skeleton h-[360px] rounded-[20px]" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="studio-card grid min-h-[360px] place-items-center bg-white px-6 text-center dark:bg-[#171a21]">
            <div>
              <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-white dark:text-slate-950">
                <ImagePlus className="size-5" />
              </div>
              <p className="mt-4 text-[15px] font-semibold text-slate-950 dark:text-stone-50">暂无商品</p>
              <p className="mt-2 text-sm text-slate-500 dark:text-stone-400">先创建一个商品，用于绑定生成结果和参考图。</p>
              <Button className="studio-button mt-5 rounded-2xl bg-slate-950 text-white" onClick={openCreate}>
                <PackagePlus className="size-4" />
                新建商品
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {items.map((product) => {
              const cover = productCover(product);
              const score = productScore(product);
              return (
                <article key={product.id} className="studio-card group overflow-hidden bg-white dark:bg-[#171a21]">
                  <div className="relative aspect-[4/3] overflow-hidden bg-slate-100 dark:bg-white/[0.04]">
                    {cover ? (
                      <img src={cover} alt={product.name} className="h-full w-full object-cover transition duration-300 group-hover:scale-105" />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center bg-[radial-gradient(circle_at_30%_20%,rgba(79,124,255,.18),transparent_35%),linear-gradient(135deg,#F8FAFC,#E9EEF8)] text-slate-400">
                        <ImagePlus className="size-10" />
                      </div>
                    )}
                    <div className="absolute inset-0 bg-gradient-to-t from-slate-950/74 via-transparent to-transparent opacity-0 transition duration-300 group-hover:opacity-100" />
                    <div className="absolute inset-x-0 bottom-0 translate-y-4 p-4 opacity-0 transition duration-300 group-hover:translate-y-0 group-hover:opacity-100">
                      <div className="flex flex-wrap gap-2">
                        <Button asChild size="sm" className="studio-button rounded-2xl bg-white text-slate-950 hover:bg-white">
                          <Link href="/image">
                            <WandSparkles className="size-3.5" />
                            快速生成
                          </Link>
                        </Button>
                        <Button size="sm" variant="outline" className="studio-button rounded-2xl border-white/70 bg-white/92 text-slate-700 shadow-none hover:bg-white" onClick={() => openEdit(product)}>
                          <Pencil className="size-3.5" />
                          编辑
                        </Button>
                        <Button asChild size="sm" variant="outline" className="studio-button rounded-2xl border-white/70 bg-white/92 text-slate-700 shadow-none hover:bg-white">
                          <Link href={`/image-library?search=${encodeURIComponent(product.name)}`}>
                            <History className="size-3.5" />
                            历史图片
                          </Link>
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="truncate text-[17px] font-semibold text-slate-950 dark:text-stone-50">{product.name}</h2>
                        <div className="mt-1 flex flex-wrap gap-1.5 text-[12px] text-slate-500 dark:text-stone-400">
                          {product.sku ? <span>SKU {product.sku}</span> : <span>暂无 SKU</span>}
                          {product.brand ? <span>{product.brand}</span> : null}
                          {product.category ? <span>{product.category}</span> : null}
                        </div>
                      </div>
                      <Badge variant="success" className="shrink-0 rounded-full">启用</Badge>
                    </div>

                    <p className="mt-3 line-clamp-2 min-h-10 text-[13px] leading-5 text-slate-600 dark:text-stone-300">
                      {product.selling_points || product.notes || "未填写卖点。建议补充核心痛点、使用场景和转化理由。"}
                    </p>

                    <div className="mt-4 grid grid-cols-3 gap-2">
                      <div className="rounded-2xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
                        <div className="text-[11px] text-slate-500">最近生成</div>
                        <div className="mt-1 truncate text-[13px] font-semibold text-slate-950 dark:text-stone-50">
                          {formatTime(product.updated_at)}
                        </div>
                      </div>
                      <div className="rounded-2xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
                        <div className="text-[11px] text-slate-500">AI评分</div>
                        <div className="mt-1 text-[13px] font-semibold text-[#16C784]">{score}</div>
                      </div>
                      <div className="rounded-2xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
                        <div className="text-[11px] text-slate-500">使用次数</div>
                        <div className="mt-1 text-[13px] font-semibold text-slate-950 dark:text-stone-50">
                          {Math.max(product.references.length, 1)}
                        </div>
                      </div>
                    </div>

                    <div className="mt-4 flex items-center justify-between gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="studio-button h-9 rounded-2xl border-black/[0.06] bg-white px-3 text-xs shadow-none"
                        onClick={() => pickReference(product.id)}
                      >
                        <ImagePlus className="size-3.5" />
                        参考图
                      </Button>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          className="studio-button inline-flex size-9 items-center justify-center rounded-2xl text-slate-400 hover:bg-[#4F7CFF]/10 hover:text-[#315be8]"
                          onClick={() => openEdit(product)}
                          aria-label="编辑商品"
                        >
                          <Pencil className="size-4" />
                        </button>
                        <button
                          type="button"
                          className="studio-button inline-flex size-9 items-center justify-center rounded-2xl text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                          onClick={() => void handleArchive(product)}
                          aria-label="归档商品"
                        >
                          <Archive className="size-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      <Dialog open={Boolean(editing)} onOpenChange={(open) => (!open ? setEditing(null) : null)}>
        <DialogContent className="max-h-[88dvh] overflow-y-auto rounded-[20px] border-black/[0.06] bg-white p-6 shadow-[0_24px_70px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[#171a21]">
          <DialogHeader>
            <DialogTitle className="text-xl">{editing?.id ? "编辑商品" : "新建商品"}</DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="grid gap-3">
              <Input value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} placeholder="商品名称" className="h-11 rounded-2xl" />
              <div className="grid gap-3 sm:grid-cols-3">
                <Input value={editing.sku} onChange={(event) => setEditing({ ...editing, sku: event.target.value })} placeholder="SKU" className="h-11 rounded-2xl" />
                <Input value={editing.brand} onChange={(event) => setEditing({ ...editing, brand: event.target.value })} placeholder="品牌" className="h-11 rounded-2xl" />
                <Input value={editing.category} onChange={(event) => setEditing({ ...editing, category: event.target.value })} placeholder="分类" className="h-11 rounded-2xl" />
              </div>
              <Textarea value={editing.selling_points} onChange={(event) => setEditing({ ...editing, selling_points: event.target.value })} placeholder="商品卖点" className="min-h-28 rounded-2xl" />
              <Textarea value={editing.notes} onChange={(event) => setEditing({ ...editing, notes: event.target.value })} placeholder="备注" className="min-h-24 rounded-2xl" />
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" className="rounded-2xl" onClick={() => setEditing(null)}>取消</Button>
            <Button className="rounded-2xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => void saveProduct()} disabled={isSaving}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
