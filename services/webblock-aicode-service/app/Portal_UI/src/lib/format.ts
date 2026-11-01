// Formatting at render time. API values stay raw (price as string, dates as ISO-8601 UTC).

export type Currency = "USD" | "KHR";

/** Dates are shown in Cambodia time, so server and client render the same text. */
const TIME_ZONE = "Asia/Phnom_Penh";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const khr = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/**
 * "4.5" USD → "$4.50" · "18000" KHR → "18,000៛".
 * `trimZeroCents` drops ".00" on whole amounts: "0" USD → "$0" (pricing cards).
 */
export function formatPrice(
  price: string,
  currency: Currency,
  { trimZeroCents = false }: { trimZeroCents?: boolean } = {},
): string {
  const amount = Number(price);
  if (currency === "KHR") return `${khr.format(amount)}៛`;
  const text = usd.format(amount);
  return trimZeroCents ? text.replace(/\.00$/, "") : text;
}

const date = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
});

const time = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

/** "2026-10-23T08:30:00Z" → "Oct 23, 2026" */
export function formatDate(iso: string): string {
  return date.format(new Date(iso));
}

/** "2026-10-23T08:30:00Z" → "3:30 PM" */
export function formatTime(iso: string): string {
  return time.format(new Date(iso));
}

/** "2026-10-23T08:30:00Z" → "Oct 23, 2026, 3:30 PM" */
export function formatDateTime(iso: string): string {
  return `${formatDate(iso)}, ${formatTime(iso)}`;
}

const month = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, month: "short" });

/** "2026-10-01T00:00:00Z" → "Oct" */
export function formatMonth(iso: string): string {
  return month.format(new Date(iso));
}

/** "Just now", "12m ago", "2h ago", then the date. `now` is injectable for tests. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h ago`;
  return formatDate(iso);
}

const dayMonthYear = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
});

/** "2026-03-12T08:30:00Z" → "12 Mar 2026" */
export function formatDayMonthYear(iso: string): string {
  return dayMonthYear.format(new Date(iso));
}

const dayMonth = new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, month: "short", day: "numeric" });

/** "2026-11-05T08:30:00Z" → "5 Nov" */
export function formatDayMonth(iso: string): string {
  return dayMonth.format(new Date(iso));
}

const usdWhole = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/** Like formatPrice but drops ".00" on whole amounts, for headline numbers: "14920.00" → "$14,920" · "19.99" → "$19.99" */
export function formatPriceShort(price: string, currency: Currency): string {
  const amount = Number(price);
  return currency === "USD" && Number.isInteger(amount) ? usdWhole.format(amount) : formatPrice(price, currency);
}

/** Notification style: "Just now", "2 mins ago", "1 hour ago", "Yesterday", "3 days ago", then the date. */
export function formatTimeAgo(iso: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (minutes < 1) return "Just now";
  if (minutes < 60) return plural(minutes, "min");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, "hour");
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return plural(days, "day");
  return formatDate(iso);
}

const logClock = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** Log timestamps, 24-hour with milliseconds: "2026-10-22T04:33:42.118Z" → "11:33:42.118" */
export function formatLogTime(iso: string): string {
  const d = new Date(iso);
  return `${logClock.format(d)}.${String(d.getUTCMilliseconds()).padStart(3, "0")}`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "2 hours ago", "yesterday", "5 days ago". Use in Server Components so server and client agree. */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const diff = Math.max(0, now - new Date(iso).getTime());
  if (diff < HOUR) {
    const minutes = Math.max(1, Math.round(diff / MINUTE));
    return minutes === 1 ? "a minute ago" : `${minutes} minutes ago`;
  }
  if (diff < DAY) {
    const hours = Math.round(diff / HOUR);
    return hours === 1 ? "an hour ago" : `${hours} hours ago`;
  }
  const days = Math.round(diff / DAY);
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

/** "Today, 9:14 AM" / "Yesterday, 4:02 PM" / "12 Mar 2026, 9:14 AM". */
export function formatLastActive(iso: string, now: number = Date.now()): string {
  const key = (ms: number) => dayMonthYear.format(new Date(ms));
  if (key(new Date(iso).getTime()) === key(now)) return `Today, ${formatTime(iso)}`;
  if (key(new Date(iso).getTime()) === key(now - DAY)) return `Yesterday, ${formatTime(iso)}`;
  return `${formatDayMonthYear(iso)}, ${formatTime(iso)}`;
}

/** "09:00" → "9:00 AM" · "18:30" → "6:30 PM" */
export function formatClock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "PM" : "AM";
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** Whole days from `now` until `iso`, never below 0. */
export function daysUntil(iso: string, now: number = Date.now()): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - now) / DAY));
}

const dayMonthYearTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** "2026-10-06T07:32:00Z" → "6 Oct 2026, 14:32" (receipt line on the payment screen) */
export function formatDayMonthYearTime(iso: string): string {
  return dayMonthYearTime.format(new Date(iso)).replace(/^(.*?) at /, "$1, ");
}

/** "Petal & Stem" → "PS" · "Lantern Café" → "LC". Two letters for logo and avatar placeholders. */
export function initials(name: string): string {
  const words = name.match(/[\p{L}\p{N}]+/gu) ?? [];
  return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
}

/** 101 → "1m 41s" · 45 → "45s" · 3720 → "1h 2m" */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
