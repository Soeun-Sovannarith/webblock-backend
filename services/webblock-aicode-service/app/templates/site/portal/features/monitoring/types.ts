import type { PlanCode, UsageMetric } from "@/portal/features/account/types";

export type DeployStatus = "SUCCEEDED" | "FAILED";

export type Deploy = {
  id: string;
  websiteId: string;
  /** 1, 2, 3… per website. */
  number: number;
  startedBy: { fullName: string; initials: string };
  startedAt: string;
  finishedAt: string;
  durationSeconds: number;
  status: DeployStatus;
  /** Why a failed deploy stopped, or `null`. */
  errorMessage: string | null;
};

/** Is the public site reachable right now (checked by the backend). */
export type SiteStatus = {
  websiteId: string;
  online: boolean;
  /** Last 30 days, as a decimal string, e.g. "99.98". */
  uptimePercent: string;
  checkedAt: string;
};

export type DeployStats = { total: number; succeeded: number; failed: number };

/** Limits of the website's plan and how much of each is used. */
export type PlanUsage = {
  plan: PlanCode;
  planName: string;
  /** When monthly counters (redeploys) go back to 0. */
  resetsAt: string;
  metrics: UsageMetric[];
};

export type ActivityType = "DEPLOY" | "PRODUCT" | "BUSINESS_INFO";
export type ActivityOutcome = "SUCCEEDED" | "FAILED" | "CHANGED";

/** One line of the activity log (Pro and Ultra plans). */
export type ActivityEvent = {
  id: string;
  websiteId: string;
  type: ActivityType;
  outcome: ActivityOutcome;
  title: string;
  /** Extra line, e.g. why a deploy failed. */
  detail: string | null;
  occurredAt: string;
};

/**
 * What a generated site can check about itself. Deploys, uptime and plan usage live in the WebBlock platform, which
 * the site cannot reach, so the Monitor screen points to the WebBlock app for them.
 */
export type SiteHealth = {
  /** The site answered this request, so it is online. */
  online: boolean;
  /** The site's own database answered within 2 seconds. */
  databaseReachable: boolean;
  /** `null` when the database could not be read. */
  products: { total: number; shown: number } | null;
  checkedAt: string;
};
