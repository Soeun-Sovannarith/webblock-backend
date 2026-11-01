import type { ActivityEvent, Deploy, DeployStatus, PlanUsage, SiteStatus } from "@/features/monitoring/types";
import { PETAL_AND_STEM_ID } from "./session";
import { daysFromNow, hoursAgo } from "./time";

const HOUR = 3_600_000;

const maya = { fullName: "Maya Sok", initials: "MS" };
const sovannarith = { fullName: "Sovannarith Soeun", initials: "SV" };

type DeploySeed = [daysAgo: number, status: DeployStatus, durationSeconds: number, error?: string];

function toDeploys(websiteId: string, prefix: string, seeds: DeploySeed[]): Deploy[] {
  return seeds.map(([ago, status, durationSeconds, error], i) => {
    const finishedAt = new Date(Date.now() - ago * 24 * HOUR).toISOString();
    return {
      id: `${prefix}-${String(i + 1).padStart(12, "0")}`,
      websiteId,
      number: i + 1,
      startedBy: i % 3 === 2 ? sovannarith : maya,
      startedAt: new Date(new Date(finishedAt).getTime() - durationSeconds * 1000).toISOString(),
      finishedAt,
      durationSeconds,
      status,
      errorMessage: error ?? null,
    };
  });
}

/** Oldest first. Petal & Stem: 10 deploys, 2 failed (the Figma "Monitor" numbers). */
export const deploys: Deploy[] = [
  ...toDeploys(PETAL_AND_STEM_ID, "d3a1e5c2-7b4f-4e8a-9c01", [
    [38, "SUCCEEDED", 112],
    [33, "SUCCEEDED", 97],
    [27, "FAILED", 64, "Image “hero-banner.png” is over 10 MB"],
    [22, "SUCCEEDED", 104],
    [19, "FAILED", 58, "Business email is missing"],
    [16, "SUCCEEDED", 112],
    [9, "SUCCEEDED", 104],
    [6, "FAILED", 69, "Image “spring-tulips.jpg” is over 10 MB"],
    [5, "SUCCEEDED", 98],
    [2, "SUCCEEDED", 101],
  ]),
];

export const siteStatus: SiteStatus[] = [
  { websiteId: PETAL_AND_STEM_ID, online: true, uptimePercent: "99.98", checkedAt: hoursAgo(0.05) },
];

/** Plan limits per website. "products" usage is counted live from the products store. */
export const planUsage: (PlanUsage & { websiteId: string })[] = [
  {
    websiteId: PETAL_AND_STEM_ID,
    plan: "PLUS",
    planName: "Plus",
    resetsAt: daysFromNow(5),
    metrics: [
      { key: "products", label: "Products", cardLabel: "Products", used: 0, limit: 30 },
      { key: "redeploys", label: "Redeploys", cardLabel: "Redeploys this month", used: 5, limit: 5 },
      { key: "websites", label: "Websites", cardLabel: "Websites", used: 1, limit: 1 },
    ],
  },
];

/** Newest first. Only Pro and Ultra websites have an activity log, and Petal & Stem is on Plus. */
export const activity: ActivityEvent[] = [];
