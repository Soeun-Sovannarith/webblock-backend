import { z } from "zod";
import { WEEKDAYS } from "@/features/websites/schemas";
import type { OpeningHour } from "@/features/websites/types";

const PHONE = /^[+()\d\s-]{6,20}$/;
const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

const openingHour = z.object({
  day: z.enum(WEEKDAYS.map((w) => w.day) as [OpeningHour["day"], ...OpeningHour["day"][]]),
  open: z.boolean(),
  opensAt: z.string().regex(CLOCK),
  closesAt: z.string().regex(CLOCK),
});

/** The hours editor posts its seven rows as one JSON field. */
const hoursField = z
  .string()
  .transform((raw, ctx) => {
    try {
      return JSON.parse(raw) as unknown;
    } catch {
      ctx.addIssue({ code: "custom", message: "Opening hours could not be read. Try again." });
      return z.NEVER;
    }
  })
  .pipe(
    z
      .array(openingHour)
      .length(7, "Set the hours for every day of the week.")
      .refine((hours) => hours.some((h) => h.open), "Turn on at least one day you are open.")
      .refine(
        (hours) => hours.every((h) => !h.open || h.opensAt < h.closesAt),
        "Closing time must be later than opening time.",
      ),
  );

export const businessInfoInput = z.object({
  description: z
    .string()
    .trim()
    .min(10, "Describe your business in at least 10 characters.")
    .max(300, "Use 300 characters or fewer."),
  phone: z.string().trim().regex(PHONE, "Enter a valid phone number."),
  email: z.email("Enter a valid email address."),
  address: z.string().trim().min(1, "Enter your address.").max(150, "Use 150 characters or fewer."),
  // Shown as a link on the website, so only https:// links are accepted.
  mapsUrl: z
    .union([z.literal(""), z.url({ protocol: /^https$/, error: "Paste a link that starts with https://." })])
    .transform((value) => value || null),
  hours: hoursField,
});

export type BusinessInfoInput = z.infer<typeof businessInfoInput>;
