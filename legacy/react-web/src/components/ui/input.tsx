import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-10 w-full min-w-0 rounded-lg border border-slate-200 bg-white/90 px-3 py-2 text-sm text-slate-950 outline-none transition-[background-color,border-color,box-shadow,color] duration-150 file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-slate-500 selection:bg-[#0071e3] selection:text-white disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-[#0071e3]/55 focus-visible:ring-[3px] focus-visible:ring-[#0071e3]/15 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-50 dark:placeholder:text-stone-400 dark:focus-visible:border-[#0a84ff]/70 dark:focus-visible:ring-[#0a84ff]/20",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
