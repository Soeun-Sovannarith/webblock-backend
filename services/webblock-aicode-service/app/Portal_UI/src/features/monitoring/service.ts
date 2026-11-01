// Builds the Monitor screen from several monitoring endpoints.
// Nothing here knows whether data comes from the mock or from Spring.
import { getWebsite } from "@/features/websites/api";
import { getDeployStats, getPlanUsage, getSiteStatus, listActivity, listDeploys } from "./api";
import type { ActivityEvent, Deploy, DeployStats, PlanUsage, SiteStatus } from "./types";

/** A deploy row with how long that version stayed live. */
export type DeployRow = Deploy & {
  /** Milliseconds this version was live, or `null` for a failed deploy. */
  liveForMs: number | null;
  /** True for the version visitors see now. */
  current: boolean;
};

export type MonitorView = {
  domain: string | null;
  status: SiteStatus | null;
  stats: DeployStats;
  usage: PlanUsage | null;
  /** Newest first. */
  deploys: DeployRow[];
  /** Up to 7 most recent deploys, oldest first, for the dot timeline. */
  timeline: Deploy[];
  /** `null` when the plan has no activity log (Free trial, Plus). */
  activity: ActivityEvent[] | null;
};

const RECENT = 6;

export async function getMonitorView(websiteId: string, now: number = Date.now()): Promise<MonitorView> {
  const [website, status, stats, usage, page] = await Promise.all([
    getWebsite(websiteId),
    getSiteStatus(websiteId),
    getDeployStats(websiteId),
    getPlanUsage(websiteId),
    // One extra deploy so the oldest row can still work out how long it was live.
    listDeploys(websiteId, { page: 0, size: RECENT + 2 }),
  ]);
  const hasActivityLog = usage?.plan === "PRO" || usage?.plan === "ULTRA";
  const activity = hasActivityLog ? (await listActivity(websiteId, { page: 0, size: RECENT })).items : null;

  const recent = page.items;
  const live = recent.find((d) => d.status === "SUCCEEDED");
  const deploys = recent.slice(0, RECENT).map((deploy, i): DeployRow => {
    if (deploy.status === "FAILED") return { ...deploy, liveForMs: null, current: false };
    // Replaced by the next newer successful deploy, or still live.
    const newer = recent.slice(0, i).findLast((d) => d.status === "SUCCEEDED");
    const until = newer ? new Date(newer.finishedAt).getTime() : now;
    return {
      ...deploy,
      liveForMs: until - new Date(deploy.finishedAt).getTime(),
      current: deploy.id === live?.id && Boolean(status?.online),
    };
  });

  return {
    domain: website.domain,
    status,
    stats,
    usage,
    deploys,
    timeline: recent.slice(0, 7).reverse(),
    activity,
  };
}
