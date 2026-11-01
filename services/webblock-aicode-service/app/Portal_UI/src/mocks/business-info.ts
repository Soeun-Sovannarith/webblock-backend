import type { BusinessInfo } from "@/features/business-info/types";
import type { OpeningHour } from "@/features/websites/types";
import { PETAL_AND_STEM_ID } from "./session";
import { daysAgo } from "./time";

function weekdays(opensAt: string, closesAt: string, weekend: Partial<OpeningHour> = {}): OpeningHour[] {
  return (["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"] as const).map((day) => {
    const isWeekend = day === "SATURDAY" || day === "SUNDAY";
    return { day, open: !isWeekend, opensAt, closesAt, ...(isWeekend ? weekend : {}) };
  });
}

export const businessInfo: BusinessInfo[] = [
  {
    websiteId: PETAL_AND_STEM_ID,
    description:
      "Hand-tied bouquets, single stems and event flowers, arranged in our studio and delivered across town.",
    logoUrl: null,
    phone: "012 345 678",
    email: "hello@petalandstem.com",
    address: "18 Garden Lane, Phnom Penh",
    mapsUrl: "https://maps.app.goo.gl/PetalStem18",
    hours: weekdays("09:00", "18:00"),
    updatedAt: daysAgo(13),
  },
];
