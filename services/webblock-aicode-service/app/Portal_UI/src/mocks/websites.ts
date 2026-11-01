import type { Website } from "@/features/websites/types";
import { PETAL_AND_STEM_ID } from "./session";
import { hoursAgo } from "./time";

export const websites: Website[] = [
  {
    id: PETAL_AND_STEM_ID,
    name: "Petal & Stem",
    businessType: "Florist",
    status: "LIVE",
    domain: "petalandstem.webblock.com",
    thumbnailUrl: "/mock/templates/everyday-store.png",
    updatedAt: hoursAgo(2),
  },
];
