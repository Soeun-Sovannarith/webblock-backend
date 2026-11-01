// This website, as the owner portal shows it: the name from the business profile (else the home page title), the
// public host from SITE_URL (else the request), and the business type.
import { headers } from "next/headers";
import data from "../../../puck-data.json";
import { getBusinessProfile } from "@/lib/business";
import type { SiteData } from "@/lib/puck";
import { ApiError } from "@/portal/lib/api/errors";
import { siteId } from "@/portal/config/site";
import type { Website } from "./types";

const site = data as unknown as SiteData;
const home = site.pages.find((page) => page.path === "/") ?? site.pages[0];

async function publicHost(): Promise<string | null> {
  const siteUrl = process.env.SITE_URL?.trim();
  if (siteUrl) {
    try {
      return new URL(siteUrl).host;
    } catch {
      // fall through to the request's host
    }
  }
  const h = await headers();
  return h.get("x-forwarded-host")?.split(",")[0].trim() || h.get("host");
}

/** "everyday_store" → "Everyday store". */
function typeLabel(type: string | undefined): string {
  const text = (type ?? "").replace(/[_-]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "Business";
}

export async function getWebsite(websiteId: string): Promise<Website> {
  if (websiteId !== siteId()) {
    throw new ApiError({ title: "Website not found", status: 404, detail: "That website no longer exists." });
  }
  const [profile, domain] = await Promise.all([getBusinessProfile(), publicHost()]);
  return {
    id: websiteId,
    name: profile?.businessName || home?.data.root.props?.title || "Your website",
    businessType: typeLabel(profile?.businessType),
    status: "LIVE",
    domain,
    thumbnailUrl: "",
    updatedAt: profile?.updatedAt ?? new Date().toISOString(),
  };
}
