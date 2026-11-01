import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Page title (accent colour) with a one-line description and optional actions on the right. */
export function PageHeader({
  title,
  description,
  actions,
  compact = false,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  /** Smaller title and spacing (Admin Portal). */
  compact?: boolean;
  /** Pass `mb-0` when the page layout already spaces its children (e.g. `gap-[15px]`). */
  className?: string;
}) {
  return (
    <header
      className={cn(
        "flex flex-wrap justify-between gap-4",
        compact ? "mb-4 items-center" : "mb-5 items-start",
        className,
      )}
    >
      <div>
        <h1
          className={cn(
            "font-semibold text-primary",
            compact ? "text-2xl leading-8 tracking-[-0.4px]" : "text-[30px] leading-[34.5px] tracking-[-0.6px]",
          )}
        >
          {title}
        </h1>
        {description && (
          <p
            className={cn(
              "text-muted-foreground",
              compact ? "mt-0.5 text-[13px]" : "pt-1.5 text-[15px] leading-[22.5px]",
            )}
          >
            {description}
          </p>
        )}
      </div>
      {actions && <div className={cn("flex items-center", compact ? "gap-1.5" : "gap-2")}>{actions}</div>}
    </header>
  );
}
