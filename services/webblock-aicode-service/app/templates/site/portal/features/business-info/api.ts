// Data access for the website's business details: the shared catalog's "business_profiles" row through
// lib/business.ts. Hours are stored in the onboarding format ("Monday", "9:00 AM") and edited here as "MONDAY", "09:00".
import { getBusinessProfile, saveBusinessProfile, type StoredHours } from "@/lib/business";
import { storageProblem, type Photo } from "@/lib/products";
import { ApiError } from "@/portal/lib/api/errors";
import { WEEKDAYS } from "@/portal/features/websites/schemas";
import type { OpeningHour } from "@/portal/features/websites/types";
import type { BusinessInfo } from "./types";

const DEFAULT_OPENS = "09:00";
const DEFAULT_CLOSES = "18:00";

/** "9:00 AM", "6:30 pm", "18:00" → "HH:mm"; null when it can't be read. */
function to24h(value: string | undefined): string | null {
  const match = value?.trim().match(/^(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?$/i);
  if (!match) return null;
  let hours = Number(match[1]);
  const minutes = Number(match[2]);
  const suffix = match[3]?.toLowerCase()[0];
  if (suffix === "p" && hours < 12) hours += 12;
  if (suffix === "a" && hours === 12) hours = 0;
  if (hours > 23 || minutes > 59) return null;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** "18:00" → "6:00 PM", as onboarding writes times. */
function to12h(value: string): string {
  const [hours, minutes] = value.split(":").map(Number);
  return `${hours % 12 || 12}:${String(minutes).padStart(2, "0")} ${hours < 12 ? "AM" : "PM"}`;
}

/** Seven days, Monday first; a day missing from the stored hours is closed. */
function toOpeningHours(stored: StoredHours[] | null): OpeningHour[] {
  return WEEKDAYS.map(({ day, label }) => {
    const entry = stored?.find((d) => d.day?.trim().toLowerCase().slice(0, 3) === label.toLowerCase().slice(0, 3));
    return {
      day,
      open: Boolean(entry?.isOpen),
      opensAt: to24h(entry?.opens) ?? DEFAULT_OPENS,
      closesAt: to24h(entry?.closes) ?? DEFAULT_CLOSES,
    };
  });
}

function toStoredHours(hours: OpeningHour[]): StoredHours[] {
  return hours.map((h) => ({
    day: WEEKDAYS.find((w) => w.day === h.day)?.label ?? h.day,
    isOpen: h.open,
    opens: to12h(h.opensAt),
    closes: to12h(h.closesAt),
  }));
}

/** The website's business details; empty fields (and every day closed) until the owner fills them in. */
export async function getBusinessInfo(websiteId: string): Promise<BusinessInfo> {
  const profile = await getBusinessProfile();
  return {
    websiteId,
    description: profile?.description ?? "",
    logoUrl: profile?.logoUrl ?? null,
    phone: profile?.phone ?? "",
    email: profile?.email ?? "",
    address: profile?.address ?? "",
    mapsUrl: profile?.mapsUrl ?? null,
    hours: toOpeningHours(profile?.hours ?? null),
    updatedAt: profile?.updatedAt ?? new Date().toISOString(),
  };
}

export type BusinessInfoBody = Pick<BusinessInfo, "description" | "phone" | "email" | "address" | "mapsUrl" | "hours"> & {
  /** A new logo; `undefined` keeps the current one. */
  logo?: Photo;
};

/** Saves the details; the website shows them from the next page load. */
export async function updateBusinessInfo(websiteId: string, body: BusinessInfoBody): Promise<BusinessInfo> {
  try {
    await saveBusinessProfile({
      description: body.description,
      phone: body.phone,
      email: body.email,
      address: body.address,
      mapsUrl: body.mapsUrl,
      hours: toStoredHours(body.hours),
      logo: body.logo,
    });
  } catch (error) {
    const problem = storageProblem(error);
    if (problem) throw new ApiError({ title: "Logo not saved", status: 422, detail: problem });
    throw error;
  }
  return getBusinessInfo(websiteId);
}
