"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";

import { cn } from "@/lib/utils";

type MiniMapMarker = {
  id: string;
  top: number;
  height: number;
  label: string;
};

type NavigatorMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  progress: number;
  thumbTop: number;
  thumbHeight: number;
  velocity: number;
  preview: string;
  markers: MiniMapMarker[];
};

type UltraScrollNavigatorProps = {
  targetRef: RefObject<HTMLElement | null>;
  className?: string;
  miniMap?: boolean;
  previewSelector?: string;
  virtualItemCount?: number;
  virtualVisibleStart?: number;
  virtualVisibleEnd?: number;
};

const NAVIGATOR_HEIGHT = 0;
const MIN_THUMB_HEIGHT = 36;
const MAX_STRETCH = 52;
const EDGE_MAX_SPEED = 34;
const DEAD_ZONE = 0.16;

function nowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function easeOutQuart(value: number) {
  return 1 - Math.pow(1 - value, 4);
}

function defaultMetrics(): NavigatorMetrics {
  return {
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    progress: 0,
    thumbTop: 0,
    thumbHeight: MIN_THUMB_HEIGHT,
    velocity: 0,
    preview: "顶部",
    markers: [],
  };
}

function formatPercent(progress: number) {
  return `${Math.round(clamp(progress, 0, 1) * 100)}%`;
}

function getElementTopWithinScroller(element: HTMLElement, scroller: HTMLElement) {
  const elementRect = element.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  return elementRect.top - scrollerRect.top + scroller.scrollTop;
}

function getPreview(scroller: HTMLElement, selector: string) {
  const nodes = Array.from(scroller.querySelectorAll<HTMLElement>(selector));
  if (nodes.length === 0) {
    return scroller.scrollTop <= 4 ? "顶部" : "滚动中";
  }

  let active = nodes[0];
  let activeDistance = Number.POSITIVE_INFINITY;
  const anchor = scroller.scrollTop + scroller.clientHeight * 0.28;
  for (const node of nodes) {
    const top = getElementTopWithinScroller(node, scroller);
    const distance = Math.abs(top - anchor);
    if (distance < activeDistance) {
      active = node;
      activeDistance = distance;
    }
  }
  return active.dataset.scrollPreview || active.getAttribute("aria-label") || "当前位置";
}

function buildDomMarkers(scroller: HTMLElement, selector: string): MiniMapMarker[] {
  const scrollable = Math.max(1, scroller.scrollHeight - scroller.clientHeight);
  return Array.from(scroller.querySelectorAll<HTMLElement>(selector))
    .slice(0, 80)
    .map((node, index) => {
      const top = getElementTopWithinScroller(node, scroller);
      const height = Math.max(8, Math.min(28, (node.offsetHeight / Math.max(1, scroller.scrollHeight)) * 120));
      return {
        id: `${index}-${top}`,
        top: clamp(top / scrollable, 0, 1),
        height,
        label: node.dataset.scrollPreview || `位置 ${index + 1}`,
      };
    });
}

function buildVirtualMarkers(count: number, start = 0, end = 0): MiniMapMarker[] {
  const safeCount = Math.max(0, Math.min(120, Math.floor(count)));
  if (safeCount === 0) return [];
  const visibleStart = clamp(start, 0, safeCount - 1);
  const visibleEnd = clamp(end || visibleStart, visibleStart, safeCount - 1);
  return Array.from({ length: safeCount }, (_, index) => ({
    id: `virtual-${index}`,
    top: safeCount <= 1 ? 0 : index / (safeCount - 1),
    height: index >= visibleStart && index <= visibleEnd ? 18 : 6,
    label: `项目 ${index + 1}`,
  }));
}

export function UltraScrollNavigator({
  targetRef,
  className,
  miniMap = true,
  previewSelector = "[data-scroll-preview]",
  virtualItemCount,
  virtualVisibleStart,
  virtualVisibleEnd,
}: UltraScrollNavigatorProps) {
  const id = useId();
  const [isHovering, setIsHovering] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [metrics, setMetrics] = useState<NavigatorMetrics>(() => defaultMetrics());
  const rafRef = useRef<number | null>(null);
  const autoRafRef = useRef<number | null>(null);
  const inertiaRafRef = useRef<number | null>(null);
  const lastScrollRef = useRef({ top: 0, time: nowMs(), velocity: 0 });
  const pointerYRef = useRef(0);
  const targetAutoVelocityRef = useRef(0);
  const autoVelocityRef = useRef(0);
  const dragOffsetRef = useRef(0);

  const canScroll = metrics.scrollHeight > metrics.clientHeight + 2;
  const percentLabel = formatPercent(metrics.progress);
  const thumbStyle = useMemo(
    () => ({
      transform: `translate3d(0, ${metrics.thumbTop}px, 0)`,
      height: `${metrics.thumbHeight}px`,
      boxShadow: `0 0 ${isHovering || isDragging ? 28 : 18}px rgba(79,124,255,${isHovering || isDragging ? 0.38 : 0.24})`,
    }),
    [isDragging, isHovering, metrics.thumbHeight, metrics.thumbTop],
  );

  const measure = useCallback(() => {
    const scroller = targetRef.current;
    if (!scroller) return;

    const now = nowMs();
    const scrollTop = scroller.scrollTop;
    const scrollHeight = scroller.scrollHeight;
    const clientHeight = scroller.clientHeight;
    const scrollable = Math.max(1, scrollHeight - clientHeight);
    const railHeight = Math.max(1, clientHeight - NAVIGATOR_HEIGHT);
    const progress = clamp(scrollTop / scrollable, 0, 1);
    const last = lastScrollRef.current;
    const elapsed = Math.max(16, now - last.time);
    const instantVelocity = (scrollTop - last.top) / elapsed;
    const velocity = last.velocity * 0.72 + instantVelocity * 0.28;
    const baseThumbHeight = clamp((clientHeight / Math.max(clientHeight, scrollHeight)) * railHeight, MIN_THUMB_HEIGHT, railHeight);
    const stretch = clamp(Math.abs(velocity) * 38, 0, MAX_STRETCH);
    const thumbHeight = clamp(baseThumbHeight + stretch, MIN_THUMB_HEIGHT, railHeight);
    const thumbTop = clamp(progress * (railHeight - thumbHeight), 0, railHeight - thumbHeight);
    const markers =
      typeof virtualItemCount === "number"
        ? buildVirtualMarkers(virtualItemCount, virtualVisibleStart, virtualVisibleEnd)
        : buildDomMarkers(scroller, previewSelector);

    lastScrollRef.current = { top: scrollTop, time: now, velocity };
    setMetrics({
      scrollTop,
      scrollHeight,
      clientHeight,
      progress,
      thumbTop,
      thumbHeight,
      velocity,
      preview: getPreview(scroller, previewSelector),
      markers,
    });
  }, [previewSelector, targetRef, virtualItemCount, virtualVisibleEnd, virtualVisibleStart]);

  const scheduleMeasure = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = null;
      measure();
    });
  }, [measure]);

  const animateScrollTo = useCallback(
    (nextTop: number) => {
      const scroller = targetRef.current;
      if (!scroller) return;
      if (inertiaRafRef.current != null) {
        window.cancelAnimationFrame(inertiaRafRef.current);
      }
      const startTop = scroller.scrollTop;
      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const targetTop = clamp(nextTop, 0, maxTop);
      const started = nowMs();
      const duration = 420;

      const tick = (now: number) => {
        const progress = clamp((now - started) / duration, 0, 1);
        scroller.scrollTop = startTop + (targetTop - startTop) * easeOutQuart(progress);
        if (progress < 1) {
          inertiaRafRef.current = window.requestAnimationFrame(tick);
        } else {
          inertiaRafRef.current = null;
        }
      };
      inertiaRafRef.current = window.requestAnimationFrame(tick);
    },
    [targetRef],
  );

  const updateAutoVelocity = useCallback(
    (clientY: number) => {
      const scroller = targetRef.current;
      if (!scroller) return;
      const rect = scroller.getBoundingClientRect();
      const relative = clamp((clientY - rect.top) / Math.max(1, rect.height), 0, 1);
      const centered = (relative - 0.5) * 2;
      const strength = Math.abs(centered);
      targetAutoVelocityRef.current = strength < DEAD_ZONE ? 0 : Math.sign(centered) * Math.pow((strength - DEAD_ZONE) / (1 - DEAD_ZONE), 1.8) * EDGE_MAX_SPEED;
    },
    [targetRef],
  );

  const stopAutoScroll = useCallback(() => {
    targetAutoVelocityRef.current = 0;
  }, []);

  const startAutoLoop = useCallback(() => {
    if (autoRafRef.current != null) return;
    let lastTime = nowMs();
    const tick = (now: number) => {
      const scroller = targetRef.current;
      if (!scroller) {
        autoRafRef.current = null;
        return;
      }
      const dt = clamp((now - lastTime) / 16.67, 0.25, 2.4);
      lastTime = now;
      autoVelocityRef.current += (targetAutoVelocityRef.current - autoVelocityRef.current) * 0.18;
      if (Math.abs(autoVelocityRef.current) > 0.03) {
        scroller.scrollTop += autoVelocityRef.current * dt;
      }
      if (isHovering || isDragging || Math.abs(autoVelocityRef.current) > 0.05) {
        autoRafRef.current = window.requestAnimationFrame(tick);
      } else {
        autoRafRef.current = null;
      }
    };
    autoRafRef.current = window.requestAnimationFrame(tick);
  }, [isDragging, isHovering, targetRef]);

  useEffect(() => {
    const scroller = targetRef.current;
    if (!scroller) return;
    measure();
    const onScroll = () => scheduleMeasure();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    const resizeObserver = new ResizeObserver(scheduleMeasure);
    resizeObserver.observe(scroller);
    if (scroller.firstElementChild) {
      resizeObserver.observe(scroller.firstElementChild);
    }
    const mutationObserver = new MutationObserver(scheduleMeasure);
    mutationObserver.observe(scroller, { childList: true, subtree: true, attributes: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      if (rafRef.current != null) window.cancelAnimationFrame(rafRef.current);
      if (autoRafRef.current != null) window.cancelAnimationFrame(autoRafRef.current);
      if (inertiaRafRef.current != null) window.cancelAnimationFrame(inertiaRafRef.current);
    };
  }, [measure, scheduleMeasure, targetRef]);

  useEffect(() => {
    if (isHovering || isDragging) {
      startAutoLoop();
    }
  }, [isDragging, isHovering, startAutoLoop]);

  const scrollFromClientY = useCallback(
    (clientY: number, dragOffset = 0, animate = false) => {
      const scroller = targetRef.current;
      if (!scroller) return;
      const rect = scroller.getBoundingClientRect();
      const railHeight = Math.max(1, rect.height - NAVIGATOR_HEIGHT);
      const scrollable = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const top = clamp(clientY - rect.top - dragOffset, 0, railHeight - metrics.thumbHeight);
      const nextProgress = railHeight <= metrics.thumbHeight ? 0 : top / (railHeight - metrics.thumbHeight);
      const nextTop = nextProgress * scrollable;
      if (animate) {
        animateScrollTo(nextTop);
      } else {
        scroller.scrollTop = nextTop;
      }
    },
    [animateScrollTo, metrics.thumbHeight, targetRef],
  );

  if (!canScroll) {
    return null;
  }

  return (
    <div
      aria-label="Ultra Scroll Navigator"
      className={cn(
        "pointer-events-none absolute inset-y-0 right-0 z-30 flex w-10 justify-end overflow-visible",
        className,
      )}
    >
      <div
        className={cn(
          "pointer-events-auto relative h-full w-8 select-none opacity-0 transition-opacity duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
          (isHovering || isDragging) && "opacity-100",
        )}
        onPointerEnter={(event) => {
          setIsHovering(true);
          pointerYRef.current = event.clientY;
          updateAutoVelocity(event.clientY);
        }}
        onPointerMove={(event) => {
          pointerYRef.current = event.clientY;
          if (isDragging) {
            scrollFromClientY(event.clientY, dragOffsetRef.current);
            return;
          }
          updateAutoVelocity(event.clientY);
        }}
        onPointerLeave={() => {
          setIsHovering(false);
          stopAutoScroll();
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
          setIsDragging(false);
          stopAutoScroll();
        }}
      >
        <div
          className={cn(
            "absolute right-2 top-1/2 z-10 flex -translate-y-1/2 items-center gap-2 rounded-full border border-white/60 bg-white/72 px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 opacity-0 shadow-[0_18px_46px_rgba(15,23,42,0.14)] backdrop-blur-xl transition duration-[250ms] ease-[cubic-bezier(0.22,1,0.36,1)] dark:border-white/10 dark:bg-[#171a21]/76 dark:text-stone-200",
            (isHovering || isDragging) && "translate-x-0 opacity-100",
          )}
        >
          <span className="min-w-8 font-mono text-[#4F7CFF]">{percentLabel}</span>
          <span className="max-w-32 truncate">{metrics.preview}</span>
        </div>

        {miniMap ? (
          <div className="absolute bottom-5 right-5 top-5 w-3 rounded-full border border-black/[0.05] bg-white/42 shadow-[inset_0_1px_0_rgba(255,255,255,.72)] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]">
            <div
              className="absolute left-1/2 w-px -translate-x-1/2 rounded-full bg-[#4F7CFF]/35"
              style={{ top: 0, height: `${metrics.progress * 100}%` }}
            />
            {metrics.markers.map((marker) => (
              <span
                key={`${id}-${marker.id}`}
                title={marker.label}
                className="absolute left-1/2 block w-1.5 -translate-x-1/2 rounded-full bg-slate-900/20 shadow-[0_0_10px_rgba(79,124,255,0.18)] dark:bg-white/30"
                style={{
                  top: `${marker.top * 100}%`,
                  height: `${marker.height}px`,
                  transform: "translate3d(-50%, -50%, 0)",
                }}
              />
            ))}
          </div>
        ) : null}

        <button
          type="button"
          aria-label={`滚动位置 ${percentLabel}`}
          className="absolute inset-y-0 right-0 w-8 cursor-ns-resize touch-none bg-transparent"
          onPointerDown={(event) => {
            const scroller = targetRef.current;
            if (!scroller) return;
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
            setIsDragging(true);
            stopAutoScroll();
            const rect = scroller.getBoundingClientRect();
            const thumbClientTop = rect.top + metrics.thumbTop;
            const insideThumb = event.clientY >= thumbClientTop && event.clientY <= thumbClientTop + metrics.thumbHeight;
            dragOffsetRef.current = insideThumb ? event.clientY - thumbClientTop : metrics.thumbHeight / 2;
            scrollFromClientY(event.clientY, dragOffsetRef.current, !insideThumb);
          }}
        >
          <span className="absolute inset-y-4 right-[9px] w-px rounded-full bg-slate-900/8 dark:bg-white/10" />
          <span
            className={cn(
              "absolute right-[7px] block w-0.5 rounded-full border border-white/80 bg-[#4F7CFF]/70 backdrop-blur-xl transition-[width,right,background-color,box-shadow] duration-[250ms] ease-[cubic-bezier(0.22,1,0.36,1)] will-change-transform dark:border-white/20 dark:bg-[#6b8dff]/80",
              (isHovering || isDragging) && "right-[5px] w-1.5 bg-[#4F7CFF]",
            )}
            style={thumbStyle}
          />
        </button>
      </div>
    </div>
  );
}
