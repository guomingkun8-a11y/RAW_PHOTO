"use client";

import { type ComponentProps, type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Bell,
  Bot,
  Boxes,
  Clock3,
  ImageIcon,
  Library,
  LogOut,
  Menu,
  Search,
  Settings,
  Sparkles,
  UserRound,
  Users,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { logout as logoutApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, getStoredAuthSession, type StoredAuthSession } from "@/store/auth";
import { listImageConversations } from "@/store/image-conversations";

const navItems = [
  { href: "/image", label: "图片生成", detail: "AI 创作台", icon: WandSparkles },
  { href: "/products", label: "商品库", detail: "SKU 与素材", icon: Boxes },
  { href: "/prompt-templates", label: "模板中心", detail: "提示词资产", icon: Sparkles },
  { href: "/image-library", label: "历史图库", detail: "瀑布流资产", icon: Library },
  { href: "/monitoring", label: "监控看板", detail: "运行状态", icon: Activity, adminOnly: true },
  { href: "/users", label: "成员权限", detail: "团队管理", icon: Users, adminOnly: true },
];

function userInitial(session: StoredAuthSession | null) {
  const source = session?.name || session?.username || "U";
  return source.trim().slice(0, 1).toUpperCase();
}

function pathFromHref(href: string) {
  return href.split("?")[0];
}

function isActivePath(pathname: string | null, href: string) {
  const path = pathFromHref(href);
  return pathname === path || Boolean(pathname?.startsWith(`${path}/`));
}

function IconButton({
  label,
  children,
  className,
  ...props
}: ComponentProps<"button"> & {
  label: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "studio-button inline-flex size-10 shrink-0 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-600 shadow-[0_10px_24px_rgba(15,23,42,0.05)] hover:border-[#4F7CFF]/20 hover:bg-[#4F7CFF]/[0.08] hover:text-slate-950 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/20 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300 dark:hover:bg-white/[0.1] dark:hover:text-white",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function StudioAssistantDock() {
  return (
    <div
      className="fixed right-5 bottom-5 z-40 hidden sm:block"
    >
      <button
        type="button"
        className="studio-button group flex items-center gap-3 rounded-2xl border border-[#4F7CFF]/20 bg-slate-950 px-4 py-3 text-left text-white shadow-[0_18px_46px_rgba(79,124,255,0.28)] hover:bg-slate-900 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/25 dark:bg-white dark:text-slate-950"
      >
        <span className="relative flex size-10 items-center justify-center rounded-xl bg-white/12 dark:bg-slate-950/8">
          <Bot className="size-5 transition-transform duration-300 group-hover:rotate-6" />
          <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-[#16C784]" />
        </span>
        <span>
          <span className="block text-sm font-semibold">AI 助手</span>
          <span className="block text-xs text-white/70 dark:text-slate-500">优化 Prompt 与平台尺寸</span>
        </span>
      </button>
    </div>
  );
}

export function TopNav({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [taskCount, setTaskCount] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadSession = async () => {
      const item = await getStoredAuthSession();
      if (!cancelled) {
        setSession(item);
      }
    };
    void loadSession();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    const loadTasks = async () => {
      try {
        const items = await listImageConversations();
        if (!cancelled) {
          setTaskCount(items.length);
        }
      } catch {
        if (!cancelled) {
          setTaskCount(0);
        }
      }
    };
    void loadTasks();
    const timer = window.setInterval(() => void loadTasks(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pathname]);

  const visibleNavItems = useMemo(
    () => navItems.filter((item) => !item.adminOnly || session?.role === "admin"),
    [session?.role],
  );

  if (pathname === "/login" || pathname === "/login/") {
    return <>{children}</>;
  }

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = searchValue.trim();
    const target = query ? `/image-library?search=${encodeURIComponent(query)}` : "/image-library";
    const currentPath = typeof window !== "undefined" ? window.location.pathname.replace(/\/$/, "") : "";
    if (currentPath === "/image-library") {
      window.history.pushState(null, "", target);
      window.dispatchEvent(new CustomEvent("image-library-search", { detail: { query } }));
      return;
    }
    router.push(target);
  };

  const handleLogout = async () => {
    try {
      await logoutApi();
    } catch {
      // Local logout still clears stale sessions.
    }
    await clearStoredAuthSession();
    router.replace("/login");
  };

  const handleNavItemClick = (href: string) => {
    void href;
    setMobileOpen(false);
  };

  const navList = (
    <nav className="flex flex-col gap-2">
      {visibleNavItems.map((item) => {
        const Icon = item.icon;
        const active = isActivePath(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={() => handleNavItemClick(item.href)}
            className={cn(
              "group relative flex min-h-[58px] items-center gap-3 rounded-2xl px-3.5 py-3 text-left transition-[background-color,color,transform,border-color,box-shadow] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/20",
              active
                ? "border border-[#4F7CFF]/20 bg-[#4F7CFF]/10 text-slate-950 shadow-[0_14px_32px_rgba(79,124,255,0.12)] dark:bg-[#4F7CFF]/18 dark:text-white"
                : "border border-transparent text-slate-600 hover:border-black/[0.04] hover:bg-[linear-gradient(135deg,rgba(79,124,255,.10),rgba(109,94,247,.06))] hover:text-slate-950 dark:text-stone-300 dark:hover:border-white/10 dark:hover:text-white",
            )}
          >
            <span
              className={cn(
                "flex size-10 shrink-0 items-center justify-center rounded-xl transition-transform duration-300 group-hover:rotate-6",
                active
                  ? "bg-[#4F7CFF] text-white"
                  : "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-stone-300",
              )}
            >
              <Icon className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold">{item.label}</span>
              <span className="mt-0.5 block truncate text-[12px] text-slate-500 dark:text-stone-400">
                {item.detail}
              </span>
            </span>
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen bg-[#F8FAFC] dark:bg-[#0f1115]">
      <header className="sticky top-0 z-40 flex h-[var(--studio-nav-height)] items-center border-b border-black/[0.06] bg-[#F8FAFC]/88 px-4 backdrop-blur-2xl dark:border-white/10 dark:bg-[#0f1115]/84 sm:px-5">
        <div className="mx-auto grid h-14 w-full max-w-[1680px] grid-cols-[auto_1fr_auto] items-center gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              className="studio-button inline-flex size-10 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-700 shadow-[0_10px_24px_rgba(15,23,42,0.05)] lg:hidden"
              onClick={() => setMobileOpen((open) => !open)}
              aria-label="打开导航"
            >
              {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
            </button>
            <Link
              href="/image"
              className="group flex min-w-0 items-center gap-3 rounded-2xl pr-2 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/20"
              aria-label="AI Image Studio"
            >
              <span className="relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-slate-950 text-white shadow-[0_16px_36px_rgba(17,24,39,0.18)] dark:bg-white dark:text-slate-950">
                <img src="/jiakemei-mark.svg" alt="" className="size-8 rounded-xl" />
                <span className="absolute inset-0 bg-[#4F7CFF]/0 transition group-hover:bg-[#4F7CFF]/10" />
              </span>
              <span className="hidden min-w-0 flex-col leading-none sm:flex">
                <span className="truncate text-[16px] font-semibold text-slate-950 dark:text-stone-50">
                  AI Image Studio
                </span>
                <span className="mt-1 truncate text-[12px] font-medium text-slate-500 dark:text-stone-400">
                  AI Creative Workspace
                </span>
              </span>
            </Link>
          </div>

          <form
            onSubmit={submitSearch}
            className="relative mx-auto hidden h-12 w-full max-w-[560px] items-center rounded-2xl border border-black/[0.06] bg-white px-4 text-slate-700 shadow-[0_12px_30px_rgba(15,23,42,0.05)] transition focus-within:border-[#4F7CFF]/35 focus-within:ring-[4px] focus-within:ring-[#4F7CFF]/10 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200 md:flex"
          >
            <Search className="pointer-events-none size-4 text-slate-400" />
            <input
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="搜索商品、历史、模板、图片"
              className="h-full min-w-0 flex-1 bg-transparent px-3 text-[14px] outline-none placeholder:text-slate-500 dark:placeholder:text-stone-400"
            />
            <span className="rounded-lg border border-black/[0.06] bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-400 dark:border-white/10 dark:bg-white/[0.06]">
              ⌘ K
            </span>
          </form>

          <div className="flex min-w-0 items-center justify-end gap-2">
            <Popover>
              <PopoverTrigger asChild>
                <IconButton label="全局搜索" className="md:hidden">
                  <Search className="size-4" />
                </IconButton>
              </PopoverTrigger>
              <PopoverContent align="end" sideOffset={12} className="w-[min(92vw,380px)] rounded-2xl border-black/[0.06] p-3 shadow-[0_24px_70px_rgba(15,23,42,0.16)]">
                <form onSubmit={submitSearch} className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                  <input
                    value={searchValue}
                    onChange={(event) => setSearchValue(event.target.value)}
                    placeholder="搜索商品、历史、模板、图片"
                    autoFocus
                    className="h-11 w-full rounded-2xl border border-black/[0.06] bg-slate-50 pl-9 pr-3 text-sm text-slate-950 outline-none transition focus:border-[#4F7CFF]/40 focus:bg-white focus:ring-[3px] focus:ring-[#4F7CFF]/10"
                  />
                </form>
              </PopoverContent>
            </Popover>

            <Popover>
              <PopoverTrigger asChild>
                <IconButton label="通知">
                  <Bell className="size-4" />
                </IconButton>
              </PopoverTrigger>
              <PopoverContent align="end" sideOffset={12} className="w-[300px] rounded-2xl border-black/[0.06] p-2 shadow-[0_24px_70px_rgba(15,23,42,0.16)]">
                <div className="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-white/[0.06]">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-950 dark:text-stone-50">
                    <span className="size-2 rounded-full bg-[#16C784]" />
                    GPU 与 API 正常
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
                    图片任务会自动同步到历史图库，可随时收藏、下载或重新生成。
                  </p>
                </div>
              </PopoverContent>
            </Popover>

            <Button
              asChild
              size="sm"
              className="studio-button hidden h-10 rounded-2xl bg-slate-950 px-3 text-[13px] text-white shadow-[0_14px_32px_rgba(17,24,39,0.14)] hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-stone-100 sm:inline-flex"
            >
              <Link href="/image-library" title="最近任务">
                <Clock3 className="size-4" />
                最近任务 {taskCount}
              </Link>
            </Button>

            <ThemeToggle />

            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label="用户菜单"
                  title="用户菜单"
                  className="studio-button ml-0.5 inline-flex size-10 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-slate-950 to-slate-800 text-sm font-semibold text-white shadow-[0_16px_36px_rgba(17,24,39,0.18)] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/20 dark:from-white dark:to-stone-100 dark:text-slate-950"
                >
                  {session ? userInitial(session) : <UserRound className="size-4" />}
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" sideOffset={12} className="w-[270px] rounded-2xl border-black/[0.06] p-2 shadow-[0_24px_70px_rgba(15,23,42,0.16)]">
                <div className="px-3 py-3">
                  <div className="truncate text-sm font-semibold text-slate-950 dark:text-stone-50">
                    {session?.name || session?.username || "用户"}
                  </div>
                  <div className="mt-1 text-xs text-slate-500 dark:text-stone-400">
                    {session?.role === "admin" ? "管理员" : "成员"}
                  </div>
                </div>
                <div className="my-1 h-px bg-slate-200 dark:bg-white/10" />
                <Link
                  href="/image"
                  className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-700 transition hover:bg-slate-100 dark:text-stone-200 dark:hover:bg-white/[0.08]"
                >
                  <ImageIcon className="size-4" />
                  创作工作台
                </Link>
                <Link
                  href="/monitoring"
                  className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-700 transition hover:bg-slate-100 dark:text-stone-200 dark:hover:bg-white/[0.08]"
                >
                  <Settings className="size-4" />
                  运行设置
                </Link>
                <button
                  type="button"
                  onClick={() => void handleLogout()}
                  className="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm text-rose-600 transition hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-950/30"
                >
                  <LogOut className="size-4" />
                  退出登录
                </button>
              </PopoverContent>
            </Popover>
          </div>
        </div>
      </header>

      <aside className="fixed left-5 top-[96px] bottom-5 z-30 hidden w-[224px] flex-col rounded-[20px] border border-black/[0.06] bg-white/82 p-3 shadow-[0_18px_44px_rgba(15,23,42,0.08)] backdrop-blur-2xl dark:border-white/10 dark:bg-white/[0.055] lg:flex">
        <div className="mb-3 rounded-2xl bg-slate-950 p-3 text-white dark:bg-white dark:text-slate-950">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Zap className="size-4 text-[#4F7CFF]" />
            Creative OS
          </div>
            <div className="mt-2 text-xs leading-5 text-white/70 dark:text-slate-500">
            为电商设计师准备的每日 AI 工作台。
          </div>
        </div>
        {navList}
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-x-3 top-[86px] z-50 rounded-[20px] border border-black/[0.06] bg-white p-3 shadow-[0_24px_70px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[#171a21] lg:hidden">
          {navList}
        </div>
      ) : null}

      <div className="min-h-[calc(100dvh_-_var(--studio-nav-height))] lg:pl-[264px]">{children}</div>
    </div>
  );
}
