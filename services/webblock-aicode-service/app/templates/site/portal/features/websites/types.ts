// Only the website fields the Owner Portal reads (the full type lives in the main WebBlock app).
export type WebsiteStatus = "LIVE" | "DRAFT" | "SUSPENDED";

export type Website = {
  id: string;
  name: string;
  businessType: string;
  status: WebsiteStatus;
  /** Public host, e.g. "petalandstem.webblock.com". `null` until the first deploy. */
  domain: string | null;
  thumbnailUrl: string;
  updatedAt: string;
};

export type Weekday =
  | "MONDAY"
  | "TUESDAY"
  | "WEDNESDAY"
  | "THURSDAY"
  | "FRIDAY"
  | "SATURDAY"
  | "SUNDAY";

/** `opensAt` / `closesAt` are 24-hour "HH:mm" strings. */
export type OpeningHour = {
  day: Weekday;
  open: boolean;
  opensAt: string;
  closesAt: string;
};
