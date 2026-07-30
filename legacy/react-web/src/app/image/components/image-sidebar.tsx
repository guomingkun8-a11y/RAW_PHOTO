"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquarePlus, Pencil, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getImageConversationStats, type ImageConversation } from "@/store/image-conversations";

type ImageSidebarProps = {
  conversations: ImageConversation[];
  isLoadingHistory: boolean;
  selectedConversationId: string | null;
  onCreateDraft: () => void;
  onClearHistory: () => void | Promise<void>;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void | Promise<void>;
  onRenameConversation: (id: string, title: string) => void | Promise<void>;
  formatConversationTime: (value: string) => string;
  hideActionButtons?: boolean;
};

export function ImageSidebar({
  conversations,
  isLoadingHistory,
  selectedConversationId,
  onCreateDraft,
  onClearHistory,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
  formatConversationTime,
  hideActionButtons = false,
}: ImageSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingId]);

  const startRename = useCallback((conversation: ImageConversation, event: React.MouseEvent) => {
    event.stopPropagation();
    setEditingId(conversation.id);
    setEditingTitle(conversation.title);
  }, []);

  const commitRename = useCallback(() => {
    const trimmed = editingTitle.trim();
    if (editingId && trimmed) {
      void onRenameConversation(editingId, trimmed);
    }
    setEditingId(null);
    setEditingTitle("");
  }, [editingId, editingTitle, onRenameConversation]);

  const cancelRename = useCallback(() => {
    setEditingId(null);
    setEditingTitle("");
  }, []);

  return (
    <aside className="h-full min-h-0 overflow-hidden">
      <div className="flex h-full min-h-0 flex-col gap-4">
        {!hideActionButtons ? (
          <div className="space-y-3">
            <div>
              <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">任务历史</h2>
              <p className="mt-1 text-[13px] leading-5 text-slate-500 dark:text-stone-400">
                每次生成都是可复用的创作上下文。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button className="studio-button h-11 flex-1 rounded-2xl bg-slate-950 text-white shadow-[0_16px_36px_rgba(17,24,39,0.16)] hover:bg-slate-800" onClick={onCreateDraft}>
                <MessageSquarePlus className="size-4" />
                新建任务
              </Button>
              <Button
                variant="outline"
                className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white px-3 text-slate-600 shadow-none hover:bg-rose-50 hover:text-rose-600"
                onClick={() => void onClearHistory()}
                disabled={conversations.length === 0}
                aria-label="清空历史"
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </div>
        ) : null}

        <div
          className={cn(
            "min-h-0 flex-1 overflow-y-auto [scrollbar-color:rgba(79,124,255,.35)_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#4F7CFF]/35 [&::-webkit-scrollbar-track]:bg-transparent",
            hideActionButtons ? "space-y-2 pr-0" : "space-y-3 pr-1",
          )}
        >
          {isLoadingHistory ? (
            <div className="space-y-3 px-1 py-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="studio-skeleton h-[86px] rounded-2xl" />
              ))}
            </div>
          ) : conversations.length === 0 ? (
            <div className="rounded-[20px] border border-dashed border-slate-300 bg-white px-4 py-5 text-sm leading-6 text-slate-500 dark:border-white/10 dark:bg-white/[0.04]">
              <div className="mb-3 flex size-10 items-center justify-center rounded-2xl bg-[#4F7CFF]/10 text-[#4F7CFF]">
                <Sparkles className="size-5" />
              </div>
              还没有生成记录。提交第一个任务后，这里会沉淀历史、状态和可复用配置。
            </div>
          ) : (
            conversations.map((conversation) => {
              const active = conversation.id === selectedConversationId;
              const stats = getImageConversationStats(conversation);
              return (
                <article
                  key={conversation.id}
                  className={cn(
                    "group relative w-full rounded-[20px] border text-left transition-[transform,border-color,background-color,box-shadow] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] hover:-translate-y-0.5",
                    hideActionButtons ? "px-4 py-3.5" : "px-4 py-3.5",
                    active
                      ? "border-[#4F7CFF]/35 bg-[#4F7CFF]/10 text-slate-950 shadow-[0_16px_36px_rgba(79,124,255,0.12)] dark:text-white"
                      : "border-black/[0.06] bg-white text-slate-700 hover:border-[#4F7CFF]/20 hover:bg-white dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-200",
                  )}
                >
                  <button type="button" onClick={() => onSelectConversation(conversation.id)} className="block w-full pr-10 text-left">
                    <div className={cn("truncate font-semibold", hideActionButtons ? "text-base" : "text-[15px]")}>
                      {editingId === conversation.id ? (
                        <input
                          ref={editInputRef}
                          value={editingTitle}
                          onChange={(event) => setEditingTitle(event.target.value)}
                          onBlur={commitRename}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") commitRename();
                            if (event.key === "Escape") cancelRename();
                          }}
                          onClick={(event) => event.stopPropagation()}
                          className="w-full truncate rounded-xl border border-[#4F7CFF]/30 bg-white px-2 py-1 text-sm outline-none focus:ring-[3px] focus:ring-[#4F7CFF]/15"
                        />
                      ) : (
                        <span className="truncate">{conversation.title}</span>
                      )}
                    </div>
                    <div className={cn("mt-1.5 text-xs", active ? "text-slate-600 dark:text-stone-300" : "text-slate-400")}>
                      {conversation.turns.length} 轮 / {formatConversationTime(conversation.updatedAt)}
                    </div>
                    {stats.running > 0 || stats.queued > 0 ? (
                      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold">
                        {stats.running > 0 ? (
                          <span className="rounded-full bg-[#4F7CFF]/10 px-2 py-1 text-[#315be8]">处理中 {stats.running}</span>
                        ) : null}
                        {stats.queued > 0 ? (
                          <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">排队 {stats.queued}</span>
                        ) : null}
                      </div>
                    ) : null}
                  </button>
                  <div className="absolute right-2 top-3 flex items-center gap-0.5 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={(event) => startRename(conversation, event)}
                      className="studio-button inline-flex size-8 items-center justify-center rounded-xl text-slate-400 hover:bg-[#4F7CFF]/10 hover:text-[#315be8]"
                      aria-label="重命名会话"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void onDeleteConversation(conversation.id)}
                      className="studio-button inline-flex size-8 items-center justify-center rounded-xl text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      aria-label="删除会话"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </div>
    </aside>
  );
}
