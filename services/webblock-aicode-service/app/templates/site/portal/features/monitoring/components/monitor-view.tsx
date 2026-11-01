import { Box, Data, Send2 } from "iconsax-reactjs";
import { PageHeader } from "@/portal/components/common/page-header";
import { formatTime } from "@/portal/lib/format";
import { cn } from "@/portal/lib/utils";
import type { MonitorView as MonitorData } from "../service";
import { LiveBadge, MonitorCard, MonitorCardTitle, StatCard } from "./monitor-cards";

/**
 * Owner Portal → Monitor, with what the site can check itself: online, its database, its products. Deploy history,
 * uptime and plan usage are kept by the WebBlock platform and shown in the WebBlock app.
 */
export function MonitorView({ data }: { data: MonitorData }) {
  const { domain, health, links } = data;
  const productsValue = health.products ? String(health.products.total) : "—";
  const productsHint = health.products
    ? `${health.products.shown} shown on your website`
    : "Shows once the database answers.";

  return (
    <>
      <PageHeader
        className="mb-0"
        title="Monitor"
        description={domain ? `Here’s how ${domain} is doing right now.` : "Here’s how your website is doing right now."}
      />

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <MonitorCard className="lg:col-span-2 xl:col-span-1">
          <div className="flex items-start gap-3 border-b border-border pb-4">
            <span
              aria-hidden
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full",
                health.databaseReachable ? "bg-success-soft" : "bg-danger-soft",
              )}
            >
              <span className={cn("size-3 rounded-full", health.databaseReachable ? "bg-success" : "bg-danger")} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="font-sans text-[17px] leading-6 font-semibold text-foreground">
                {health.databaseReachable ? "Your website is online" : "Your website is online, but its database is not answering"}
              </h2>
              <p className="truncate text-xs text-muted-foreground">{domain ?? "Your website"}</p>
            </div>
            <LiveBadge online={health.online} />
          </div>
          <p className="pt-4 text-sm text-muted-foreground">
            {health.databaseReachable
              ? "Visitors see your latest products and business details."
              : "Visitors see the products saved with your pages until the database answers again. Products and business details can’t be changed meanwhile."}{" "}
            Checked at {formatTime(health.checkedAt)}.
          </p>
        </MonitorCard>

        <StatCard icon={<Data />} label="Database" value={health.databaseReachable ? "Connected" : "Unreachable"} />
        <MonitorCard className="justify-center gap-1">
          <span aria-hidden className="mb-4 flex size-11 items-center justify-center rounded-xl bg-primary-soft text-primary [&_svg]:size-[22px]">
            <Box />
          </span>
          <p className="text-sm text-muted-foreground">Products</p>
          <p className="font-heading text-[34px] leading-10 font-semibold tracking-[-0.5px] text-foreground">{productsValue}</p>
          <p className="text-xs text-muted-foreground">{productsHint}</p>
        </MonitorCard>
      </div>

      <MonitorCard>
        <MonitorCardTitle hint="Deploy history, uptime and plan usage are kept by WebBlock, not by your website.">
          Deploys and plan usage
        </MonitorCardTitle>
        <p className="flex items-center gap-2 pt-4 text-sm text-muted-foreground">
          <Send2 aria-hidden className="size-4 shrink-0" />
          {links.editor ? (
            <span>
              See them in the{" "}
              <a href={links.editor} className="font-semibold text-primary hover:underline">
                WebBlock editor
              </a>
              {links.plans && (
                <>
                  {" "}or{" "}
                  <a href={links.plans} className="font-semibold text-primary hover:underline">
                    your plan
                  </a>
                </>
              )}
              .
            </span>
          ) : (
            <span>See them in the WebBlock app, where you deploy your website.</span>
          )}
        </p>
      </MonitorCard>
    </>
  );
}
