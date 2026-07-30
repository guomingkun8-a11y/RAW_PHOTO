"use client";

import { useEffect, useState } from "react";
import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { logout as logoutApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, getStoredAuthSession, type StoredAuthSession } from "@/store/auth";

export function HeaderActions({ className }: { className?: string; showGithubText?: boolean }) {
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const item = await getStoredAuthSession();
      if (!cancelled) {
        setSession(item);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = async () => {
    try {
      await logoutApi();
    } catch {
      // Local logout should still succeed if the session has already expired.
    }
    await clearStoredAuthSession();
    router.replace("/login");
  };

  return (
    <div className={cn("flex items-center gap-2 sm:gap-3", className)}>
      <ThemeToggle />
      {session ? (
        <>
          <span className="hidden rounded-lg border border-slate-200/80 bg-white/80 px-2.5 py-1 text-[11px] font-medium text-slate-600 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300 sm:inline-block">
            {session.name || session.username || "用户"} / {session.role === "admin" ? "管理员" : "员工"}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 rounded-lg border-slate-200/80 bg-white/80 px-2 text-xs text-slate-600 shadow-none hover:bg-white dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200 dark:hover:bg-white/[0.1]"
            onClick={() => void handleLogout()}
          >
            <LogOut className="size-3.5" />
            <span className="hidden sm:inline">退出</span>
          </Button>
        </>
      ) : null}
    </div>
  );
}
