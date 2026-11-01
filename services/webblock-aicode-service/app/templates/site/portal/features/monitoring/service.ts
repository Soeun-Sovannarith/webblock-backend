// Builds the Monitor screen from what the site can check about itself.
import { appLinks, type AppLinks } from "@/portal/config/site";
import { getWebsite } from "@/portal/features/websites/api";
import { getSiteHealth } from "./api";
import type { SiteHealth } from "./types";

export type MonitorView = {
  domain: string | null;
  health: SiteHealth;
  links: AppLinks;
};

export async function getMonitorView(websiteId: string): Promise<MonitorView> {
  const [website, health] = await Promise.all([getWebsite(websiteId), getSiteHealth()]);
  return { domain: website.domain, health, links: appLinks() };
}
