import type { ReactNode } from "react";
import { Global } from "iconsax-reactjs";
import type { PlanCode, UsageMetric } from "@/portal/features/account/types";
import { cn } from "@/portal/lib/utils";
import type { Deploy, DeployStats } from "../types";

// Presentational cards of the Monitor screen. Server Components: no state, numbers come from the service.

export function MonitorCard({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cn("flex flex-col rounded-[20px] border border-border bg-background p-5 shadow-surface", className)}>
      {children}
    </section>
  );
}

export function MonitorCardTitle({ children, hint, action }: { children: ReactNode; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <h2 className="font-sans text-[17px] leading-6 font-semibold text-foreground">{children}</h2>
        {hint && <p className="pt-0.5 text-xs leading-[18px] text-muted-foreground">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

/** Uptime / Deploy success tile: icon, label, big number. */
export function StatCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <MonitorCard className="justify-center gap-1">
      <span aria-hidden className="mb-4 flex size-11 items-center justify-center rounded-xl bg-primary-soft text-primary [&_svg]:size-[22px]">
        {icon}
      </span>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="font-heading text-[34px] leading-10 font-semibold tracking-[-0.5px] text-foreground">{value}</p>
    </MonitorCard>
  );
}

export function LiveBadge({ online }: { online: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-[5px] rounded-full px-2 text-xs font-medium [&_svg]:size-3.5",
        online ? "bg-success-soft text-success" : "bg-danger-soft text-danger",
      )}
    >
      <Global aria-hidden />
      {online ? "Live" : "Offline"}
    </span>
  );
}

/** Green/red dots for the last deploys, oldest on the left. */
export function DeployTimeline({ deploys }: { deploys: Deploy[] }) {
  const failed = deploys.filter((d) => d.status === "FAILED").length;
  return (
    <div>
      <div
        role="img"
        aria-label={`Last ${deploys.length} deploys: ${deploys.length - failed} succeeded, ${failed} failed`}
        className="relative flex items-center justify-between py-1"
      >
        <span aria-hidden className="absolute inset-x-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-border" />
        {deploys.map((deploy) => (
          <span
            key={deploy.id}
            aria-hidden
            className={cn(
              "relative size-2 rounded-full ring-4 ring-background",
              deploy.status === "SUCCEEDED" ? "bg-success" : "bg-danger",
            )}
          />
        ))}
      </div>
      <div className="flex justify-between pt-2 text-[11px] text-muted-foreground">
        <span>Last {deploys.length} deploys</span>
        <span>Latest</span>
      </div>
    </div>
  );
}

const PLAN_BADGE: Record<PlanCode, string> = {
  FREE_TRIAL: "bg-muted text-muted-foreground",
  PLUS: "bg-tier-plus-soft text-tier-plus",
  PRO: "bg-tier-pro-soft text-tier-pro",
  ULTRA: "bg-tier-ultra-soft text-tier-ultra",
};

export function PlanBadge({ plan, name }: { plan: PlanCode; name: string }) {
  return (
    <span className={cn("rounded-md px-2 py-0.5 text-[10px] leading-4 font-semibold tracking-wide uppercase", PLAN_BADGE[plan])}>
      {name}
    </span>
  );
}

type Tone = "ok" | "near" | "full";

const TONE = {
  ok: { stroke: "var(--color-brand)", text: "text-brand", note: "text-muted-foreground" },
  near: { stroke: "var(--color-warning)", text: "text-warning", note: "text-warning" },
  full: { stroke: "var(--color-danger)", text: "text-danger", note: "text-danger" },
} satisfies Record<Tone, { stroke: string; text: string; note: string }>;

/** Donut showing `used of limit`. Turns amber at 80% and red at the limit. */
export function UsageRing({ metric }: { metric: UsageMetric }) {
  const ratio = metric.limit > 0 ? Math.min(1, metric.used / metric.limit) : 1;
  const left = Math.max(0, metric.limit - metric.used);
  const tone: Tone = ratio >= 1 ? "full" : ratio >= 0.8 ? "near" : "ok";
  const radius = 48;
  const circumference = 2 * Math.PI * radius;
  const note = tone === "full" ? "Limit reached" : tone === "near" ? `${left} left · near limit` : `${left} left`;

  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <p className="text-[13px] font-medium text-foreground">{metric.cardLabel}</p>
      <div
        role="img"
        aria-label={`${metric.cardLabel}: ${metric.used} of ${metric.limit} used, ${note.toLowerCase()}`}
        className="relative size-[116px]"
      >
        <svg viewBox="0 0 116 116" aria-hidden className="size-full -rotate-90">
          <circle cx="58" cy="58" r={radius} fill="none" strokeWidth="11" stroke="var(--color-track)" />
          <circle
            cx="58"
            cy="58"
            r={radius}
            fill="none"
            strokeWidth="11"
            strokeLinecap={ratio > 0 && ratio < 1 ? "round" : "butt"}
            stroke={TONE[tone].stroke}
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - ratio)}
          />
        </svg>
        <span aria-hidden className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={cn("font-heading text-[22px] leading-7 font-semibold", TONE[tone].text)}>{metric.used}</span>
          <span className="text-[11px] text-muted-foreground">of {metric.limit}</span>
        </span>
      </div>
      <p className={cn("text-xs", TONE[tone].note)}>{note}</p>
    </div>
  );
}

/** Two bars: succeeded vs failed deploys. */
export function DeployResults({ stats }: { stats: DeployStats }) {
  const bars = [
    { label: "Succeeded", count: stats.succeeded, bar: "bg-success", dot: "bg-success" },
    { label: "Failed", count: stats.failed, bar: "bg-danger", dot: "bg-danger" },
  ];
  const max = Math.max(1, stats.succeeded, stats.failed);
  const percent = (count: number) => (stats.total ? Math.round((count / stats.total) * 100) : 0);

  return (
    <MonitorCard>
      <MonitorCardTitle>Deploy results</MonitorCardTitle>
      <p className="flex items-baseline gap-2 pt-4">
        <span className="font-heading text-[28px] leading-8 font-semibold text-foreground">{stats.total}</span>
        <span className="text-xs text-muted-foreground">total deploys</span>
      </p>
      <div
        role="img"
        aria-label={`${stats.succeeded} succeeded, ${stats.failed} failed`}
        className="mt-auto grid grid-cols-2 border-b border-border pt-6"
      >
        {bars.map((bar) => (
          <div key={bar.label} aria-hidden className="flex h-[96px] flex-col items-center justify-end gap-1">
            <span className="text-[13px] font-semibold text-foreground">{bar.count}</span>
            <span
              className={cn("w-[52px] rounded-t-md", bar.bar)}
              style={{ height: `${Math.max(bar.count ? 8 : 2, (bar.count / max) * 64)}px` }}
            />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 pt-2 text-center">
        {bars.map((bar) => (
          <div key={bar.label}>
            <p className="inline-flex items-center gap-1.5 text-xs text-foreground">
              <span aria-hidden className={cn("size-1.5 rounded-full", bar.dot)} />
              {bar.label}
            </p>
            <p className="text-[11px] text-muted-foreground">{percent(bar.count)}% of deploys</p>
          </div>
        ))}
      </div>
    </MonitorCard>
  );
}
