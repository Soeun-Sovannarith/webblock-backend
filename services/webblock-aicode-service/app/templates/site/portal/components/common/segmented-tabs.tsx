"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/portal/lib/utils";

export type SegmentedTab = {
  label: string;
  href: string;
  /** Number shown after the label (e.g. websites per status). */
  count?: number;
  /** Set when the active tab comes from the URL query instead of the path (`?status=live`). */
  active?: boolean;
};

const VARIANT = {
  /** White pill on a grey track (app screens). */
  track: {
    nav: "w-fit max-w-full items-center self-start bg-segment-track",
    active: "bg-background font-semibold text-brand-deep shadow-tab",
    idle: "font-medium text-muted-foreground hover:text-foreground",
  },
  /** Blue filled pill in a bordered track (admin portal). */
  filled: {
    nav: "mb-6 border border-border bg-surface",
    active: "bg-primary font-medium text-primary-foreground",
    idle: "font-medium text-muted-foreground hover:text-foreground",
  },
} as const;

/**
 * Pill segmented tabs. Each tab is a link, so the active tab lives in the URL: the path by default,
 * or the page decides with `active` when tabs are query filters (`?status=live`).
 */
export function SegmentedTabs({
  tabs,
  label,
  variant = "track",
  size = "md",
  className,
}: {
  tabs: SegmentedTab[];
  /** Accessible name of the tab group. */
  label?: string;
  variant?: keyof typeof VARIANT;
  /** "sm" for table toolbars (13px). */
  size?: "md" | "sm";
  className?: string;
}) {
  const pathname = usePathname();
  const v = VARIANT[variant];

  return (
    <nav aria-label={label} className={cn("inline-flex rounded-full p-1", v.nav, className)}>
      {tabs.map((tab) => {
        const active = tab.active ?? pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            scroll={false}
            aria-current={active ? "page" : undefined}
            className={cn(
              "inline-flex items-center justify-center gap-2 rounded-full whitespace-nowrap transition-[background-color,color,box-shadow] outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
              size === "sm" ? "px-3 py-[3px] text-xs" : variant === "track" ? "h-9 min-w-[100px] px-4 text-sm leading-[21px]" : "px-4 py-1.5 text-sm",
              active ? v.active : v.idle,
            )}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span
                className={cn(
                  "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-semibold tabular-nums",
                  active ? "bg-brand text-primary-foreground" : "bg-background text-muted-foreground",
                )}
              >
                {tab.count}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
