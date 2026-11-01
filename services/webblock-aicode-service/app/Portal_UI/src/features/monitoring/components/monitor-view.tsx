import type { ReactNode } from "react";
import Link from "next/link";
import { Activity, Box, CloseCircle, Edit2, Send2, TickCircle } from "iconsax-reactjs";
import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { appLinks } from "@/config/site";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDayMonthYear, formatDuration, formatRelativeTime, formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { DeployRow, MonitorView as MonitorData } from "../service";
import type { ActivityEvent, ActivityType } from "../types";
import {
  DeployResults,
  DeployTimeline,
  LiveBadge,
  MonitorCard,
  MonitorCardTitle,
  PlanBadge,
  StatCard,
  UsageRing,
} from "./monitor-cards";

const DAY = 86_400_000;

function dateTime(iso: string) {
  return `${formatDayMonthYear(iso)}, ${formatTime(iso)}`;
}

/** "2 days (current)", "3 days", "5 hours", "—" for failed deploys. */
function liveFor(row: DeployRow): string {
  if (row.liveForMs === null) return "—";
  const days = Math.round(row.liveForMs / DAY);
  const hours = Math.max(1, Math.round(row.liveForMs / 3_600_000));
  const text = days >= 1 ? `${days} ${days === 1 ? "day" : "days"}` : `${hours} ${hours === 1 ? "hour" : "hours"}`;
  return row.current ? `${text} (current)` : text;
}

/** Owner Portal → Monitor. Plus and Free trial show recent deploys; Pro and Ultra add the activity log. */
export function MonitorView({ data, websiteId }: { data: MonitorData; websiteId: string }) {
  const { status, stats, usage, deploys, timeline, activity, domain } = data;
  const last = deploys[0];
  const successRate = stats.total ? `${Math.round((stats.succeeded / stats.total) * 100)}%` : "—";

  return (
    <>
      <PageHeader
        className="mb-0"
        title="Monitor"
        description={domain ? `Here’s how ${domain} is doing today.` : "Here’s how your website is doing today."}
      />

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <MonitorCard className="lg:col-span-2 xl:col-span-1">
          <div className="flex items-start gap-3 border-b border-border pb-4">
            <span
              aria-hidden
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full",
                status?.online ? "bg-success-soft" : "bg-danger-soft",
              )}
            >
              <span className={cn("size-3 rounded-full", status?.online ? "bg-success" : "bg-danger")} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="font-sans text-[17px] leading-6 font-semibold text-foreground">
                {status === null
                  ? "Your website isn’t live yet"
                  : status.online
                    ? "Your website is online"
                    : "Your website is offline"}
              </h2>
              <p className="truncate text-xs text-muted-foreground">{domain ?? "Deploy it to get a web address."}</p>
            </div>
            {status && <LiveBadge online={status.online} />}
          </div>
          {last ? (
            <div className="flex flex-col gap-4 pt-4">
              <div>
                <p className="text-xs text-muted-foreground">Last deploy</p>
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-heading text-[22px] leading-8 font-semibold text-foreground">
                    {formatRelativeTime(last.finishedAt)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    Deploy {last.number} · {dateTime(last.finishedAt)}
                  </span>
                </p>
              </div>
              <DeployTimeline deploys={timeline} />
            </div>
          ) : (
            <p className="pt-4 text-sm text-muted-foreground">No deploys yet. Deploy from the editor to put your website online.</p>
          )}
        </MonitorCard>

        <StatCard icon={<Activity />} label="Uptime" value={status ? `${status.uptimePercent}%` : "—"} />
        <StatCard icon={<TickCircle />} label="Deploy success" value={successRate} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <MonitorCard>
          {usage ? (
            <>
              <MonitorCardTitle
                hint={`${usage.planName} plan · resets on ${formatDayMonthYear(usage.resetsAt).replace(/ \d{4}$/, "")}`}
                action={<PlanBadge plan={usage.plan} name={usage.planName} />}
              >
                Plan usage
              </MonitorCardTitle>
              <div className="grid flex-1 grid-cols-1 items-center gap-6 pt-5 sm:grid-cols-3">
                {usage.metrics.map((metric) => (
                  <UsageRing key={metric.key} metric={metric} />
                ))}
              </div>
            </>
          ) : (
            <>
              <MonitorCardTitle>Plan usage</MonitorCardTitle>
              <p className="pt-4 text-sm text-muted-foreground">
                Usage shows here once the website is on a plan.{" "}
                <Link href={appLinks.plans} className="font-semibold text-primary hover:underline">
                  View plans
                </Link>
              </p>
            </>
          )}
        </MonitorCard>
        <DeployResults stats={stats} />
      </div>

      <div className={cn("grid gap-4", activity && "xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]")}>
        {activity && <ActivityLog events={activity} />}
        <RecentDeploys rows={deploys} compact={Boolean(activity)} websiteId={websiteId} />
      </div>
    </>
  );
}

function TableCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-[20px] border border-border bg-background shadow-surface">
      <h2 className="border-b border-border px-5 py-4 font-sans text-[17px] leading-6 font-semibold text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

const head = "h-auto px-5 py-3 text-xs leading-normal";
const cell = "h-[54px] px-5 py-2 text-[13px]";

function RecentDeploys({ rows, compact, websiteId }: { rows: DeployRow[]; compact: boolean; websiteId: string }) {
  return (
    <TableCard title="Recent deploys">
      {rows.length === 0 ? (
        <EmptyState
          icon={<Send2 />}
          title="No deploys yet"
          description="When you deploy your website, each deploy and its result shows here."
          action={
            <Link href={appLinks.editor(websiteId)} className="text-sm font-semibold text-primary hover:underline">
              Open the editor
            </Link>
          }
        />
      ) : (
        <Table aria-label="Recent deploys">
          <TableHeader>
            <TableRow>
              <TableHead className={head}>Deploy</TableHead>
              {!compact && <TableHead className={head}>Started by</TableHead>}
              <TableHead className={head}>Date</TableHead>
              {!compact && <TableHead className={head}>Live for</TableHead>}
              <TableHead className={head}>Status</TableHead>
              <TableHead className={cn(head, "text-right")}>Duration</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell className={cn(cell, "font-semibold tabular-nums")}>{row.number}</TableCell>
                {!compact && (
                  <TableCell className={cell}>
                    <span className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className="flex size-6 items-center justify-center rounded-full bg-brand-soft text-[9px] font-bold text-brand-deep"
                      >
                        {row.startedBy.initials}
                      </span>
                      {row.startedBy.fullName}
                    </span>
                  </TableCell>
                )}
                <TableCell className={cn(cell, "whitespace-nowrap")}>{dateTime(row.finishedAt)}</TableCell>
                {!compact && <TableCell className={cell}>{liveFor(row)}</TableCell>}
                <TableCell className={cell}>
                  <StatusBadge status={row.status} />
                </TableCell>
                <TableCell className={cn(cell, "text-right tabular-nums")}>{formatDuration(row.durationSeconds)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </TableCard>
  );
}

const ACTIVITY_TYPE = {
  DEPLOY: { label: "Deploy", className: "bg-brand-soft text-brand-strong" },
  PRODUCT: { label: "Product", className: "bg-success-soft text-success" },
  BUSINESS_INFO: { label: "Business info", className: "bg-tier-ultra-soft text-tier-ultra" },
} satisfies Record<ActivityType, { label: string; className: string }>;

function ActivityIcon({ event }: { event: ActivityEvent }) {
  const [icon, className] =
    event.outcome === "FAILED"
      ? [<CloseCircle key="i" />, "bg-danger-soft text-danger"]
      : event.type === "DEPLOY"
        ? [<TickCircle key="i" />, "bg-success-soft text-success"]
        : event.type === "PRODUCT"
          ? [<Box key="i" />, "bg-brand-soft text-brand"]
          : [<Edit2 key="i" />, "bg-tier-ultra-soft text-tier-ultra"];
  return (
    <span aria-hidden className={cn("flex size-7 shrink-0 items-center justify-center rounded-full [&_svg]:size-3.5", className)}>
      {icon}
    </span>
  );
}

function ActivityLog({ events }: { events: ActivityEvent[] }) {
  return (
    <TableCard title="Recent activity log">
      {events.length === 0 ? (
        <EmptyState
          icon={<Activity />}
          title="No activity yet"
          description="Deploys and changes to your products and business details show here."
        />
      ) : (
        <Table aria-label="Recent activity">
          <TableHeader>
            <TableRow>
              <TableHead className={head}>Update</TableHead>
              <TableHead className={head}>Type</TableHead>
              <TableHead className={cn(head, "text-right")}>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.map((event) => {
              const type = ACTIVITY_TYPE[event.type];
              return (
                <TableRow key={event.id}>
                  <TableCell className={cell}>
                    <span className="flex items-center gap-2.5">
                      <ActivityIcon event={event} />
                      <span className="min-w-0">
                        <span className="block text-foreground">{event.title}</span>
                        {event.detail && <span className="block text-xs text-danger">{event.detail}</span>}
                      </span>
                    </span>
                  </TableCell>
                  <TableCell className={cell}>
                    <span className={cn("rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap", type.className)}>
                      {type.label}
                    </span>
                  </TableCell>
                  <TableCell className={cn(cell, "text-right whitespace-nowrap")}>{dateTime(event.occurredAt)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </TableCard>
  );
}
