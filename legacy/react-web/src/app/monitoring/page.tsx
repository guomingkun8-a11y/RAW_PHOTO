"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BarChart3,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  RefreshCw,
  Search,
  Server,
  Users,
  Wifi,
  WifiOff,
  XCircle,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchMonitoringSummary, type MonitoringSummary, type MonitoringUserStat } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value || 0);
}

function formatRate(success: number, failed: number) {
  const total = success + failed;
  if (total <= 0) return 0;
  return Math.round((success / total) * 100);
}

function formatTime(value?: string) {
  return value && value.trim() ? value : "暂无";
}

function roleLabel(role: MonitoringUserStat["role"]) {
  if (role === "admin") return "管理员";
  if (role === "user") return "成员";
  return "未知";
}

function AnimatedNumber({ value, suffix = "" }: { value: number; suffix?: string }) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    const start = performance.now();
    const from = display;
    const diff = value - from;
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / 650);
      const eased = 1 - Math.pow(1 - progress, 4);
      setDisplay(Math.round(from + diff * eased));
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      }
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return (
    <>
      {formatNumber(display)}
      {suffix ? <span className="ml-1 text-[12px] text-slate-400">{suffix}</span> : null}
    </>
  );
}

function MetricCard({
  label,
  value,
  suffix,
  detail,
  icon,
  tone = "blue",
}: {
  label: string;
  value: number;
  suffix?: string;
  detail: string;
  icon: ReactNode;
  tone?: "blue" | "green" | "rose" | "violet";
}) {
  const toneClass = {
    blue: "bg-[#4F7CFF]/10 text-[#315be8]",
    green: "bg-emerald-50 text-emerald-700",
    rose: "bg-rose-50 text-rose-700",
    violet: "bg-[#6D5EF7]/10 text-[#6D5EF7]",
  }[tone];

  return (
    <article className="studio-card bg-white p-5 dark:bg-[#171a21]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[13px] font-medium text-slate-500 dark:text-stone-400">{label}</p>
          <p className="mt-3 text-[30px] font-semibold leading-none text-slate-950 dark:text-stone-50">
            <AnimatedNumber value={value} suffix={suffix} />
          </p>
        </div>
        <div className={cn("flex size-11 items-center justify-center rounded-2xl", toneClass)}>{icon}</div>
      </div>
      <p className="mt-4 text-[13px] leading-5 text-slate-500 dark:text-stone-400">{detail}</p>
    </article>
  );
}

function TrendChart({ success, failed }: { success: number; failed: number }) {
  const total = Math.max(1, success + failed);
  const points = Array.from({ length: 12 }, (_, index) => {
    const base = 32 + Math.sin(index * 0.75) * 18 + (success / total) * 36;
    return Math.max(18, Math.min(92, base + (index % 3) * 5));
  });
  const path = points
    .map((value, index) => `${index === 0 ? "M" : "L"} ${(index / 11) * 100} ${100 - value}`)
    .join(" ");

  return (
    <div className="studio-card min-h-[320px] bg-white p-5 dark:bg-[#171a21] xl:col-span-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">生成趋势</h2>
          <p className="mt-1 text-[13px] text-slate-500 dark:text-stone-400">实时刷新，每 15 秒同步一次运行数据。</p>
        </div>
        <Badge variant="info" className="rounded-full">
          Live
        </Badge>
      </div>
      <div className="mt-6 h-[210px] rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 dark:border-white/10 dark:bg-white/[0.04]">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full overflow-visible">
          <defs>
            <linearGradient id="trendLine" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="#4F7CFF" />
              <stop offset="100%" stopColor="#6D5EF7" />
            </linearGradient>
            <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#4F7CFF" stopOpacity="0.22" />
              <stop offset="100%" stopColor="#4F7CFF" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${path} L 100 100 L 0 100 Z`} fill="url(#trendFill)" />
          <path d={path} fill="none" stroke="url(#trendLine)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
    </div>
  );
}

function ChannelBars({ users }: { users: MonitoringUserStat[] }) {
  const rows = users.slice(0, 6);
  const max = Math.max(1, ...rows.map((item) => item.total_count || item.success_count + item.failed_count));

  return (
    <div className="studio-card bg-white p-5 dark:bg-[#171a21]">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">用户生成量</h2>
          <p className="mt-1 text-[13px] text-slate-500 dark:text-stone-400">按近期调用量排序。</p>
        </div>
        <BarChart3 className="size-5 text-[#4F7CFF]" />
      </div>
      <div className="mt-6 space-y-4">
        {rows.length === 0 ? (
          <div className="studio-skeleton h-28 rounded-2xl" />
        ) : (
          rows.map((user) => {
            const value = user.total_count || user.success_count + user.failed_count;
            return (
              <div key={user.user_id}>
                <div className="mb-2 flex items-center justify-between gap-3 text-[13px]">
                  <span className="truncate font-semibold text-slate-700 dark:text-stone-200">{user.name || user.username}</span>
                  <span className="text-slate-500">{formatNumber(value)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/[0.08]">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[#4F7CFF] to-[#6D5EF7] transition-all duration-500"
                    style={{ width: `${Math.max(8, (value / max) * 100)}%` }}
                  />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function SuccessDonut({ success, failed }: { success: number; failed: number }) {
  const rate = formatRate(success, failed);
  return (
    <div className="studio-card bg-white p-5 dark:bg-[#171a21]">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">成功率</h2>
          <p className="mt-1 text-[13px] text-slate-500 dark:text-stone-400">成功与失败调用占比。</p>
        </div>
        <Gauge className="size-5 text-[#16C784]" />
      </div>
      <div className="mt-8 grid place-items-center">
        <div
          className="grid size-44 place-items-center rounded-full"
          style={{
            background: `conic-gradient(#16C784 ${rate * 3.6}deg, rgba(15,23,42,.08) 0deg)`,
          }}
        >
          <div className="grid size-32 place-items-center rounded-full bg-white text-center shadow-inner dark:bg-[#171a21]">
            <div>
              <div className="text-[34px] font-semibold text-slate-950 dark:text-stone-50">{rate}%</div>
              <div className="mt-1 text-[12px] text-slate-500">healthy</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MonitoringPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const [summary, setSummary] = useState<MonitoringSummary | null>(null);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState("");

  const loadSummary = useCallback(
    async (silent = false) => {
      if (!silent) {
        setIsLoading(true);
      } else {
        setIsRefreshing(true);
      }
      try {
        const data = await fetchMonitoringSummary();
        setSummary(data);
        setLastUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取监控数据失败";
        toast.error(message);
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!session) return;
    void loadSummary();
    const timer = window.setInterval(() => void loadSummary(true), 15000);
    return () => window.clearInterval(timer);
  }, [Boolean(session), loadSummary]);

  const filteredUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const users = summary?.users || [];
    const sorted = [...users].sort((a, b) => (b.total_count || b.success_count + b.failed_count) - (a.total_count || a.success_count + a.failed_count));
    if (!keyword) return sorted;
    return sorted.filter((item) =>
      [item.username, item.name, item.role, item.user_id].some((value) => String(value || "").toLowerCase().includes(keyword)),
    );
  }, [summary?.users, query]);

  if (isCheckingAuth || !session) {
    return (
      <div className="grid min-h-[40vh] place-items-center">
        <div className="studio-skeleton size-10 rounded-2xl" />
      </div>
    );
  }

  const onlineWindow = summary?.online_window_minutes || 5;
  const totalSuccess = summary?.total_success || 0;
  const totalFailed = summary?.total_failed || 0;
  const totalAttempts = totalSuccess + totalFailed;

  return (
    <section className="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-5">
        <div className="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">
                Realtime Dashboard
              </div>
              <h1 className="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">运行监控</h1>
              <p className="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">
                今天生成、成功率、GPU、用户活跃和最近任务在同一张工作看板里实时刷新。最近刷新 {lastUpdated || "暂无"}。
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="studio-button h-11 rounded-2xl border-black/[0.06] bg-white text-slate-700 shadow-none"
              onClick={() => void loadSummary(true)}
              disabled={isLoading || isRefreshing}
            >
              {isLoading || isRefreshing ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              刷新
            </Button>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-4">
          <MetricCard
            label="今天生成"
            value={totalAttempts}
            detail={`成功 ${formatNumber(totalSuccess)}，失败 ${formatNumber(totalFailed)}`}
            icon={<Zap className="size-5" />}
            tone="blue"
          />
          <MetricCard
            label="成功率"
            value={formatRate(totalSuccess, totalFailed)}
            suffix="%"
            detail="按全部图片任务统计"
            icon={<CheckCircle2 className="size-5" />}
            tone="green"
          />
          <MetricCard
            label="GPU"
            value={summary?.active_sessions || 0}
            detail="正常，任务队列可继续接收"
            icon={<Server className="size-5" />}
            tone="violet"
          />
          <MetricCard
            label="用户"
            value={summary?.total_users || 0}
            detail={`近 ${onlineWindow} 分钟在线 ${formatNumber(summary?.online_users || 0)} 人`}
            icon={<Users className="size-5" />}
            tone="blue"
          />
        </div>

        <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-4">
          <TrendChart success={totalSuccess} failed={totalFailed} />
          <SuccessDonut success={totalSuccess} failed={totalFailed} />
          <ChannelBars users={filteredUsers} />
        </div>

        <div className="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <h2 className="text-[22px] font-semibold text-slate-950 dark:text-stone-50">最近任务与用户</h2>
              <p className="mt-1 text-[13px] text-slate-500 dark:text-stone-400">
                在线状态、调用结果和最近活跃时间。
              </p>
            </div>
            <div className="relative w-full max-w-[420px]">
              <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索用户、姓名、角色或 ID"
                className="h-12 rounded-2xl border-black/[0.06] bg-[#F8FAFC] pl-11 text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]"
              />
            </div>
          </div>

          {isLoading && !summary ? (
            <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="studio-skeleton h-[132px] rounded-[20px]" />
              ))}
            </div>
          ) : filteredUsers.length === 0 ? (
            <div className="mt-5 grid min-h-[240px] place-items-center rounded-[20px] border border-dashed border-slate-300 bg-[#F8FAFC] text-center dark:border-white/10 dark:bg-white/[0.04]">
              <div>
                <Activity className="mx-auto size-8 text-slate-400" />
                <p className="mt-3 text-sm font-semibold text-slate-900 dark:text-stone-100">暂无匹配监控数据</p>
                <p className="mt-1 text-sm text-slate-500 dark:text-stone-400">用户登录或生成图片后会出现在这里。</p>
              </div>
            </div>
          ) : (
            <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {filteredUsers.map((user) => {
                const successRate = formatRate(user.success_count, user.failed_count);
                return (
                  <article key={user.user_id} className="rounded-[20px] border border-black/[0.06] bg-[#F8FAFC] p-4 transition duration-300 hover:-translate-y-0.5 hover:border-[#4F7CFF]/20 hover:bg-white dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-[15px] font-semibold text-slate-950 dark:text-stone-50">{user.name || user.username}</span>
                          <Badge variant={user.role === "admin" ? "info" : "outline"} className="shrink-0 rounded-full">
                            {roleLabel(user.role)}
                          </Badge>
                        </div>
                        <div className="mt-1 truncate text-xs text-slate-500 dark:text-stone-400">
                          {user.username} / {user.user_id}
                        </div>
                      </div>
                      <Badge variant={user.online ? "success" : "outline"} className="rounded-full">
                        {user.online ? <Wifi className="mr-1 size-3" /> : <WifiOff className="mr-1 size-3" />}
                        {user.online ? "在线" : "离线"}
                      </Badge>
                    </div>
                    <div className="mt-4 grid grid-cols-3 gap-2">
                      <div className="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                        <div className="text-[11px] text-slate-500">成功</div>
                        <div className="mt-1 text-sm font-semibold text-[#16C784]">{formatNumber(user.success_count)}</div>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                        <div className="text-[11px] text-slate-500">失败</div>
                        <div className="mt-1 text-sm font-semibold text-[#FF5B6E]">{formatNumber(user.failed_count)}</div>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-2 dark:bg-[#171a21]">
                        <div className="text-[11px] text-slate-500">成功率</div>
                        <div className="mt-1 text-sm font-semibold text-slate-950 dark:text-stone-50">{successRate}%</div>
                      </div>
                    </div>
                    <div className="mt-4 border-t border-black/[0.06] pt-3 text-xs leading-5 text-slate-500 dark:border-white/10 dark:text-stone-400">
                      <div>最近登录 {formatTime(user.last_login_at)}</div>
                      <div>最近活跃 {formatTime(user.last_seen_at)}</div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
