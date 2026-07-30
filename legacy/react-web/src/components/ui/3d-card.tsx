"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

type CardContainerProps = React.ComponentProps<"div"> & {
  rotation?: number;
};

function normalizeDepth(value: number | string) {
  return typeof value === "number" ? `${value}px` : value;
}

export function CardContainer({
  children,
  className,
  rotation = 8,
  onMouseMove,
  onMouseLeave,
  style,
  ...props
}: CardContainerProps) {
  const ref = React.useRef<HTMLDivElement | null>(null);

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    onMouseMove?.(event);
    if (event.defaultPrevented || !ref.current) return;

    const rect = ref.current.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const rotateY = ((x / rect.width) - 0.5) * rotation;
    const rotateX = ((0.5 - y / rect.height) * rotation);

    ref.current.style.setProperty("--card-rotate-x", `${rotateX.toFixed(2)}deg`);
    ref.current.style.setProperty("--card-rotate-y", `${rotateY.toFixed(2)}deg`);
  };

  const handleMouseLeave = (event: React.MouseEvent<HTMLDivElement>) => {
    onMouseLeave?.(event);
    if (!ref.current) return;
    ref.current.style.setProperty("--card-rotate-x", "0deg");
    ref.current.style.setProperty("--card-rotate-y", "0deg");
  };

  return (
    <div
      ref={ref}
      className={cn("group/card relative transform-gpu", className)}
      style={{ perspective: "1100px", ...style }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardBody({
  children,
  className,
  style,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "relative transform-gpu transition-[transform,box-shadow,border-color,background-color] duration-200 ease-out will-change-transform",
        className,
      )}
      style={{
        transform:
          "rotateX(var(--card-rotate-x, 0deg)) rotateY(var(--card-rotate-y, 0deg))",
        transformStyle: "preserve-3d",
        ...style,
      }}
      {...props}
    >
      {children}
    </div>
  );
}

type CardItemProps<T extends React.ElementType = "div"> = {
  as?: T;
  translateZ?: number | string;
  className?: string;
  style?: React.CSSProperties;
} & Omit<React.ComponentPropsWithoutRef<T>, "as" | "className" | "style">;

export function CardItem<T extends React.ElementType = "div">({
  as,
  translateZ = 0,
  className,
  style,
  ...props
}: CardItemProps<T>) {
  const Component = as || "div";

  return (
    <Component
      className={cn("transform-gpu transition-transform duration-200 ease-out", className)}
      style={{
        transform: `translateZ(${normalizeDepth(translateZ)})`,
        ...style,
      }}
      {...props}
    />
  );
}
