import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

const art = "absolute max-w-none";

/**
 * Empty list or empty search result: icon, title, an optional hint and action.
 * `illustration` (default): dashed ring + disc art (app screens). `box`: compact dashed box (admin tables).
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  variant = "illustration",
  className,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  variant?: "illustration" | "box";
  className?: string;
}) {
  if (variant === "box") {
    return (
      <div
        className={cn(
          "mx-auto flex w-full max-w-md flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-primary/45 bg-primary-soft/50 px-6 py-8 text-center",
          className,
        )}
      >
        <span
          aria-hidden
          className="mb-1 grid size-12 place-items-center rounded-full bg-background text-brand shadow-card ring-1 ring-primary/20 [&_svg]:size-6"
        >
          {icon}
        </span>
        <p className="font-heading text-base font-semibold text-foreground">{title}</p>
        {description && <p className="text-[13px] text-muted-foreground">{description}</p>}
        {action && <div className="mt-2">{action}</div>}
      </div>
    );
  }

  return (
    <div className={cn("mx-auto flex w-full max-w-[440px] flex-col items-center gap-1.5 px-4 py-10 text-center", className)}>
      <div aria-hidden className="relative size-[104px] shrink-0">
        {/* eslint-disable @next/next/no-img-element -- decorative static art */}
        <img src="/images/empty-state/ring.png" alt="" width={104} height={104} className={`${art} top-0 left-0 size-[104px]`} />
        <img src="/images/empty-state/disc.svg" alt="" className={`${art} top-4 left-4 size-[72px]`} />
        <img src="/images/empty-state/accent-lg.svg" alt="" className={`${art} top-3.5 left-[86px] size-2`} />
        <img src="/images/empty-state/accent-sm.svg" alt="" className={`${art} top-20 left-2.5 size-[5px]`} />
        {/* eslint-enable @next/next/no-img-element */}
        <span className="absolute top-[37px] left-[37px] flex size-[30px] items-center justify-center text-brand [&_svg]:size-[30px]">
          {icon}
        </span>
      </div>
      <div className="h-2.5" />
      <h3 className="text-[17px] font-semibold">{title}</h3>
      {description && <p className="text-[13.5px] leading-[21px] text-muted-foreground">{description}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}
