// Read-only monitoring data for one website. Mock now, Spring later: only these function bodies change.
// The backend runs deploys, retries and uptime checks; the frontend only displays the results.
import { ApiError } from "@/lib/api/errors";
import { delay } from "@/lib/api/delay";
import type { Page } from "@/lib/api/types";
import { toPage, type TableQuery } from "@/lib/table-query";
import { countProducts } from "@/features/products/api";
import { activity, deploys, planUsage, siteStatus } from "@/mocks/monitoring";
import type { ActivityEvent, Deploy, DeployStats, PlanUsage, SiteStatus } from "./types";

/** GET /websites/{websiteId}/status — `null` when the website has never been deployed. */
export async function getSiteStatus(websiteId: string): Promise<SiteStatus | null> {
  await delay(150);
  return siteStatus.find((s) => s.websiteId === websiteId) ?? null;
}

/** GET /websites/{websiteId}/deploys?page=&size= — newest first. */
export async function listDeploys(websiteId: string, query: Pick<TableQuery, "page" | "size">): Promise<Page<Deploy>> {
  await delay();
  const mine = deploys.filter((d) => d.websiteId === websiteId).sort((a, b) => b.number - a.number);
  return toPage(mine, query);
}

/** GET /websites/{websiteId}/deploys/stats */
export async function getDeployStats(websiteId: string): Promise<DeployStats> {
  await delay(100);
  const mine = deploys.filter((d) => d.websiteId === websiteId);
  const succeeded = mine.filter((d) => d.status === "SUCCEEDED").length;
  return { total: mine.length, succeeded, failed: mine.length - succeeded };
}

/** GET /websites/{websiteId}/usage */
export async function getPlanUsage(websiteId: string): Promise<PlanUsage | null> {
  await delay(150);
  const usage = planUsage.find((u) => u.websiteId === websiteId);
  if (!usage) return null;
  const products = await countProducts(websiteId);
  return {
    plan: usage.plan,
    planName: usage.planName,
    resetsAt: usage.resetsAt,
    metrics: usage.metrics.map((m) => (m.key === "products" ? { ...m, used: products.total } : m)),
  };
}

/** GET /websites/{websiteId}/activity?page=&size= — newest first. Pro and Ultra plans only (403 otherwise). */
export async function listActivity(
  websiteId: string,
  query: Pick<TableQuery, "page" | "size">,
): Promise<Page<ActivityEvent>> {
  await delay();
  const plan = planUsage.find((u) => u.websiteId === websiteId)?.plan;
  if (plan !== "PRO" && plan !== "ULTRA") {
    throw new ApiError({
      title: "Not in your plan",
      status: 403,
      detail: "The activity log comes with the Pro and Ultra plans.",
    });
  }
  return toPage(
    activity.filter((a) => a.websiteId === websiteId),
    query,
  );
}
