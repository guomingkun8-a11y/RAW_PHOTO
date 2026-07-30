"use client";

import { AnimatedThemeToggler } from "@/components/ui/animated-theme-toggler";

export function ThemeToggle() {
  return (
    <AnimatedThemeToggler
      aria-label="切换主题"
      title="切换主题"
      variant="circle"
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-500 transition-[background-color,color,transform] hover:bg-white hover:text-slate-950 active:scale-95 dark:text-stone-300 dark:hover:bg-white/[0.08] dark:hover:text-white [&_svg]:size-4"
    />
  );
}
