"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, LogIn } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { login } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-auth-guard";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/store/auth";

export default function LoginPage() {
  const router = useRouter();
  const { isCheckingAuth } = useRedirectIfAuthenticated();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      toast.error("请输入用户名和密码");
      return;
    }
    setIsSubmitting(true);
    try {
      const data = await login(username.trim(), password);
      await setStoredAuthSession({
        key: data.token,
        role: data.role,
        subjectId: data.subject_id,
        username: data.username,
        name: data.name,
      });
      const params = new URLSearchParams(window.location.search);
      const next = params.get("next") || getDefaultRouteForRole(data.role);
      router.replace(next.startsWith("/") ? next : getDefaultRouteForRole(data.role));
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <section className="grid min-h-[calc(100dvh-3.5rem)] place-items-center border-t border-slate-200 bg-[#f5f5f7] px-4 py-8 dark:border-white/10 dark:bg-[#0f1115]">
      <form
        onSubmit={(event) => void handleSubmit(event)}
        className="w-full max-w-[380px] rounded-lg border border-slate-200 bg-white p-6 shadow-none dark:border-white/10 dark:bg-[#16191f]"
      >
        <div className="mb-6">
          <div className="mb-3 flex items-center gap-3">
            <img src="/jiakemei-mark.svg" alt="" className="size-11 rounded-lg" />
            <div className="min-w-0">
              <div className="text-base font-semibold text-slate-950 dark:text-stone-50">家可美</div>
              <div className="mt-1 text-xs font-medium text-slate-500 dark:text-stone-400">Jiakemei</div>
            </div>
          </div>
          <h1 className="text-lg font-semibold text-slate-950 dark:text-stone-50">登录图片工作台</h1>
          <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-stone-400">
            使用管理员或员工账号进入业务系统。
          </p>
        </div>

        <div className="grid gap-3">
          <label className="grid gap-1.5">
            <span className="text-sm font-medium text-slate-700 dark:text-stone-200">用户名</span>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              className="h-10 rounded-md border-slate-200 bg-white shadow-none dark:border-white/10 dark:bg-white/[0.04]"
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-sm font-medium text-slate-700 dark:text-stone-200">密码</span>
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              className="h-10 rounded-md border-slate-200 bg-white shadow-none dark:border-white/10 dark:bg-white/[0.04]"
            />
          </label>
        </div>

        <Button
          type="submit"
          className="mt-5 h-10 w-full rounded-lg bg-[#0071e3] text-white shadow-none hover:bg-[#0077ed] dark:bg-[#0a84ff] dark:hover:bg-[#2997ff]"
          disabled={isSubmitting}
        >
          {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <LogIn className="size-4" />}
          登录
        </Button>

      </form>
    </section>
  );
}
