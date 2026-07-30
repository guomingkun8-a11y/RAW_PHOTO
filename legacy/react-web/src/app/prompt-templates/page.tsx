"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  createPromptTemplate,
  disablePromptTemplate,
  fetchPromptTemplates,
  updatePromptTemplate,
  type PromptTemplate,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type TemplateFormState = {
  id?: number;
  name: string;
  category: string;
  content: string;
  model: string;
  size: string;
  quality: string;
  preserve_subject: boolean;
  enabled: boolean;
};

const categoryOptions = [
  { value: "main", label: "主图" },
  { value: "white", label: "白底图" },
  { value: "scene", label: "场景图" },
  { value: "detail", label: "详情图" },
  { value: "campaign", label: "活动图" },
];

const emptyForm: TemplateFormState = {
  name: "",
  category: "main",
  content: "",
  model: "gpt-image-2",
  size: "1024x1024",
  quality: "high",
  preserve_subject: true,
  enabled: true,
};

function categoryLabel(value: string) {
  return categoryOptions.find((item) => item.value === value)?.label || value || "未分类";
}

export default function PromptTemplatesPage() {
  const { isCheckingAuth, session } = useAuthGuard();
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [editing, setEditing] = useState<TemplateFormState | null>(null);

  const loadTemplates = async (search = query.trim(), selectedCategory = category) => {
    setIsLoading(true);
    try {
      const data = await fetchPromptTemplates({
        q: search,
        category: selectedCategory === "all" ? "" : selectedCategory,
      });
      setItems(data.items);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取模板库失败";
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!session) return;
    void loadTemplates("", "all");
  }, [Boolean(session)]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => {
      void loadTemplates(query.trim(), category);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [query, category, Boolean(session)]);

  const openEdit = (template: PromptTemplate) => {
    setEditing({
      id: template.id,
      name: template.name,
      category: template.category,
      content: template.content,
      model: template.model || "gpt-image-2",
      size: template.size || "1024x1024",
      quality: template.quality || "high",
      preserve_subject: template.preserve_subject,
      enabled: template.enabled,
    });
  };

  const saveTemplate = async () => {
    if (!editing?.name.trim() || !editing.content.trim()) {
      toast.error("请填写模板名称和模板内容");
      return;
    }
    setIsSaving(true);
    try {
      if (editing.id) {
        await updatePromptTemplate(editing.id, editing);
        toast.success("模板已更新");
      } else {
        await createPromptTemplate(editing);
        toast.success("模板已创建");
      }
      setEditing(null);
      await loadTemplates();
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存模板失败";
      toast.error(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDisable = async (template: PromptTemplate) => {
    try {
      await disablePromptTemplate(template.id);
      toast.success("模板已停用");
      await loadTemplates();
    } catch (error) {
      const message = error instanceof Error ? error.message : "停用模板失败";
      toast.error(message);
    }
  };

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <section className="flex h-[calc(100dvh-3.5rem)] min-h-0 flex-col overflow-hidden border-t border-slate-200 bg-[#f7f8fa] dark:border-white/10 dark:bg-[#111317]">
      <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-4 dark:border-white/10 dark:bg-[#16191f] sm:px-6">
        <div className="mx-auto flex max-w-[1280px] flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-semibold text-slate-950 dark:text-stone-50">提示词模板</h1>
              <p className="mt-1 text-sm text-slate-500 dark:text-stone-400">
                固化常用商品图场景，让团队生成风格更稳定。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="h-9 rounded-md border-slate-200 bg-white text-slate-700 shadow-none"
                onClick={() => void loadTemplates()}
                disabled={isLoading}
              >
                {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                刷新
              </Button>
              <Button className="h-9 rounded-md bg-slate-950 text-white shadow-none hover:bg-slate-800" onClick={() => setEditing({ ...emptyForm })}>
                <Plus className="size-4" />
                新建模板
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <div className="relative w-full max-w-[420px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索模板名称或内容"
                className="h-10 rounded-md border-slate-200 bg-white pl-9 text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]"
              />
            </div>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="h-10 w-[160px] rounded-md border-slate-200 bg-white text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="rounded-lg">
                <SelectItem value="all">全部分类</SelectItem>
                {categoryOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mx-auto grid max-w-[1280px] gap-3">
          {isLoading && items.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500 dark:border-white/10 dark:bg-[#16191f]">
              正在读取模板库...
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500 dark:border-white/10 dark:bg-[#16191f]">
              暂无模板，先新建一个常用场景模板。
            </div>
          ) : (
            items.map((template) => (
              <article key={template.id} className="rounded-lg border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-[#16191f]">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-sm font-semibold text-slate-950 dark:text-stone-50">{template.name}</h2>
                      <Badge variant="info" className="rounded-md">{categoryLabel(template.category)}</Badge>
                      {template.preserve_subject ? <Badge variant="success" className="rounded-md">主体保真</Badge> : null}
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-stone-400">
                      {[template.model, template.size, template.quality].filter(Boolean).join(" / ") || "未设置默认参数"}
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    <Button variant="outline" size="sm" className="h-8 rounded-md border-slate-200 bg-white px-2 text-xs shadow-none" onClick={() => openEdit(template)}>
                      <Pencil className="size-3.5" />
                      编辑
                    </Button>
                    <Button variant="outline" size="sm" className="h-8 rounded-md border-slate-200 bg-white px-2 text-xs shadow-none" onClick={() => void handleDisable(template)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
                <p className="mt-3 whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200">
                  {template.content}
                </p>
              </article>
            ))
          )}
        </div>
      </div>

      <Dialog open={Boolean(editing)} onOpenChange={(open) => (!open ? setEditing(null) : null)}>
        <DialogContent className="max-h-[88dvh] overflow-y-auto rounded-lg border-slate-200 bg-white p-6 shadow-lg dark:border-white/10 dark:bg-[#16191f]">
          <DialogHeader>
            <DialogTitle className="text-lg">{editing?.id ? "编辑模板" : "新建模板"}</DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="grid gap-3">
              <Input value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} placeholder="模板名称" className="rounded-md" />
              <div className="grid gap-3 sm:grid-cols-4">
                <Select value={editing.category} onValueChange={(value) => setEditing({ ...editing, category: value })}>
                  <SelectTrigger className="h-10 rounded-md">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {categoryOptions.map((option) => (
                      <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input value={editing.model} onChange={(event) => setEditing({ ...editing, model: event.target.value })} placeholder="模型" className="rounded-md" />
                <Input value={editing.size} onChange={(event) => setEditing({ ...editing, size: event.target.value })} placeholder="尺寸 1024x1024" className="rounded-md" />
                <Input value={editing.quality} onChange={(event) => setEditing({ ...editing, quality: event.target.value })} placeholder="质量 high" className="rounded-md" />
              </div>
              <Textarea value={editing.content} onChange={(event) => setEditing({ ...editing, content: event.target.value })} placeholder="模板提示词" className="min-h-40 rounded-md" />
              <label className="inline-flex w-fit cursor-pointer items-center gap-2 text-sm text-slate-700 dark:text-stone-200">
                <Checkbox
                  checked={editing.preserve_subject}
                  onCheckedChange={(checked) => setEditing({ ...editing, preserve_subject: checked === true })}
                  className="size-4 rounded-[4px]"
                />
                默认开启主体保真
              </label>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" className="rounded-md" onClick={() => setEditing(null)}>取消</Button>
            <Button className="rounded-md bg-slate-950 text-white hover:bg-slate-800" onClick={() => void saveTemplate()} disabled={isSaving}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
