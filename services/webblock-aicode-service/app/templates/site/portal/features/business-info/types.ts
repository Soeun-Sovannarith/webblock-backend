import type { OpeningHour } from "@/portal/features/websites/types";

/**
 * Business details shown on a website (About, Contact, Location, Opening hours).
 * Name, business type and web address belong to the website and can't be changed here.
 */
export type BusinessInfo = {
  websiteId: string;
  description: string;
  logoUrl: string | null;
  phone: string;
  email: string;
  address: string;
  /** Google Maps link shown next to the address, or `null`. */
  mapsUrl: string | null;
  /** Always seven days, Monday first. */
  hours: OpeningHour[];
  updatedAt: string;
};
