// Presentation helpers for catalog values. Pure functions, unit tested.

// The catalog stores `products.price` as NUMERIC(12,2) in US dollars (design section 6), which
// node-postgres returns as a decimal string. Null means "no price shown".
const PRICE_PATTERN = /^[0-9]{1,10}(\.[0-9]{1,2})?$/;

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Formats a catalog price ("49.5") as "$49.50". Returns null for no price or an unreadable value. */
export function formatPrice(price: string | null | undefined): string | null {
  if (price === null || price === undefined) return null;
  const text = price.trim();
  if (!PRICE_PATTERN.test(text)) return null;
  return usd.format(Number(text));
}

/** "care_instructions" -> "Care instructions". */
export function humanizeKey(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  if (words === "") return key;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Display labels for the clothing product types' attribute keys (seed/README.md).
const ATTRIBUTE_LABELS: Readonly<Record<string, string>> = {
  size: "Sizes",
  color: "Colors",
  material: "Material",
  fit: "Fit",
  care: "Care",
};
const ATTRIBUTE_ORDER = ["size", "color", "material", "fit", "care"];

export type DisplayAttribute = {
  key: string;
  label: string;
  /** Several values (sizes, colors) render as options; one value renders as text. */
  values: string[];
  isList: boolean;
};

function scalarText(value: unknown): string | null {
  if (typeof value === "string") return value.trim() === "" ? null : value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return null;
}

/**
 * Turns a product's `attributes` object into label/value rows in a stable order. Known keys come first
 * in the shop's order, then the rest alphabetically. Values that are neither scalars nor lists of
 * scalars are left out rather than shown as raw JSON.
 */
export function displayAttributes(attributes: unknown): DisplayAttribute[] {
  if (attributes === null || typeof attributes !== "object" || Array.isArray(attributes)) return [];
  const entries = Object.entries(attributes as Record<string, unknown>);
  const rank = (key: string): number => {
    const i = ATTRIBUTE_ORDER.indexOf(key);
    return i === -1 ? ATTRIBUTE_ORDER.length : i;
  };
  entries.sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));

  const rows: DisplayAttribute[] = [];
  for (const [key, value] of entries) {
    const label = ATTRIBUTE_LABELS[key] ?? humanizeKey(key);
    if (Array.isArray(value)) {
      const values = value.map(scalarText).filter((v): v is string => v !== null);
      if (values.length > 0) rows.push({ key, label, values, isList: true });
    } else {
      const text = scalarText(value);
      if (text !== null) rows.push({ key, label, values: [text], isList: false });
    }
  }
  return rows;
}

const DAY_NAMES: Readonly<Record<string, string>> = {
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
  sunday: "Sun",
};

/** One `opening_hours` range as "Mon–Fri 10:00–19:00". Unknown day names are shown as given. */
export function formatOpeningRange(range: {
  from_day: string;
  to_day: string;
  opens_at: string;
  closes_at: string;
  closes_next_day?: boolean;
}): string {
  const day = (d: string): string => DAY_NAMES[d.toLowerCase()] ?? d;
  const days =
    range.from_day === range.to_day ? day(range.from_day) : `${day(range.from_day)}–${day(range.to_day)}`;
  const close = range.closes_next_day ? `${range.closes_at} (next day)` : range.closes_at;
  return `${days} ${range.opens_at}–${close}`;
}
