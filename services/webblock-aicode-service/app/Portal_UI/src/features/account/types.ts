// Only the account types the Owner Portal reads (the full feature lives in the main WebBlock app).
export type PlanCode = "FREE_TRIAL" | "PLUS" | "PRO" | "ULTRA";

export type Profile = {
  id: string;
  fullName: string;
  /** Two letters shown when there is no photo, e.g. "SV". */
  initials: string;
  email: string;
  avatarUrl: string | null;
};

export type UsageMetric = {
  key: string;
  /** Name in the plan dialog, e.g. "Websites". */
  label: string;
  /** Shorter name in the plan card, e.g. "Website". */
  cardLabel: string;
  used: number;
  limit: number;
};
