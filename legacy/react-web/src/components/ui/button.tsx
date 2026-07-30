import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium outline-none transition-[background-color,color,border-color,box-shadow,opacity,transform] duration-150 ease-out active:scale-[0.985] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/25 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40",
  {
    variants: {
      variant: {
        default:
          "bg-[#0071e3] text-white shadow-none hover:bg-[#0077ed] active:bg-[#006edb] dark:bg-[#0a84ff] dark:hover:bg-[#2997ff]",
        destructive:
          "bg-rose-600 text-white shadow-none hover:bg-rose-500 focus-visible:ring-destructive/20 dark:bg-rose-500 dark:hover:bg-rose-400 dark:focus-visible:ring-destructive/40",
        outline:
          "border border-slate-200 bg-white/90 text-slate-700 shadow-none hover:border-slate-300 hover:bg-white hover:text-slate-950 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200 dark:hover:bg-white/[0.1] dark:hover:text-white",
        secondary:
          "bg-slate-100 text-slate-900 shadow-none hover:bg-slate-200/80 dark:bg-white/[0.08] dark:text-stone-100 dark:hover:bg-white/[0.12]",
        ghost:
          "text-slate-700 hover:bg-slate-100 hover:text-slate-950 dark:text-stone-200 dark:hover:bg-white/[0.08] dark:hover:text-white",
        link: "text-[#0071e3] underline-offset-4 hover:underline dark:text-[#0a84ff]",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 gap-1.5 rounded-lg px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-lg px-6 has-[>svg]:px-4",
        icon: "size-9 rounded-lg",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
