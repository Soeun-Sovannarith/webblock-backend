"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "@/portal/lib/utils";

type PopoverProps = {
  /** Accessible name of the trigger button. */
  label: string;
  /** Content of the trigger button (icon). */
  trigger: ReactNode;
  /** Panel content. Call `close()` after an action to dismiss the panel. */
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
  triggerClassName?: string;
  panelClassName?: string;
};

/** Small floating panel opened from a button. Closes on outside click, Escape and after actions. */
export function Popover({
  label,
  trigger,
  children,
  align = "right",
  triggerClassName,
  panelClassName,
}: PopoverProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex items-center justify-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
          triggerClassName,
        )}
      >
        {trigger}
      </button>
      {/* Always rendered so it can fade out too; `invisible` + `inert` take it out of reach while closed. */}
      <div
        id={panelId}
        inert={!open}
        className={cn(
          "absolute top-full z-30 mt-2 rounded-2xl border border-border bg-background shadow-modal",
          "transition-[opacity,translate,scale,visibility] motion-reduce:transition-none",
          align === "right" ? "right-0 origin-top-right" : "left-0 origin-top-left",
          open
            ? "visible translate-y-0 scale-100 opacity-100 duration-160 ease-out"
            : "invisible -translate-y-1 scale-[0.98] opacity-0 duration-110 ease-in",
          panelClassName,
        )}
      >
        {children(() => setOpen(false))}
      </div>
    </div>
  );
}
