// Checks seed/seed.json against the format documented in seed/README.md and the catalog design's
// constraints, so that the catalog_platform stand-in can load it as it is.
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { formatPrice } from "@/lib/format";
import { imageSrc } from "@/lib/images";

type SeedImage = { file: string; byte_size: number; alt_text: string | null; sort_order: number };
type SeedProduct = {
  product_type: string;
  category_slug: string | null;
  name: string;
  description: string | null;
  price: string | null;
  attributes: Record<string, unknown>;
  status: string;
  sort_order: number;
  images: SeedImage[];
};
type Seed = {
  format_version: number;
  business_type: string;
  business_profile: Record<string, unknown> & { business_name: string; timezone: string; opening_hours: unknown[] };
  categories: { slug: string; name: string; sort_order: number }[];
  products: SeedProduct[];
};

const root = join(import.meta.dirname, "..");
const seed = JSON.parse(readFileSync(join(root, "seed", "seed.json"), "utf8")) as Seed;
const PRODUCT_TYPES = new Set(["top", "outerwear", "trousers", "accessory"]);
const ATTRIBUTE_KEYS = new Set(["size", "color", "material", "fit", "care"]);
const WEBSITE = "4f9c2b1e-8d3a-4c5b-9e7f-0a1b2c3d4e5f";

describe("seed.json", () => {
  it("is format 1 for business type clothing_shop, with a complete profile", () => {
    expect(seed.format_version).toBe(1);
    expect(seed.business_type).toBe("clothing_shop");
    const p = seed.business_profile;
    expect(p.business_name.length).toBeGreaterThan(0);
    expect(p.business_name.length).toBeLessThanOrEqual(255);
    expect(Intl.supportedValuesOf("timeZone")).toContain(p.timezone);
    expect(Object.keys(p).sort()).toEqual(
      ["address", "business_name", "description", "email", "opening_hours", "phone", "timezone"].sort(),
    );
    for (const r of p.opening_hours) {
      expect(Object.keys(r as object).sort()).toEqual(["closes_at", "closes_next_day", "from_day", "opens_at", "to_day"]);
    }
  });

  it("has 3 to 4 categories with unique slugs", () => {
    expect(seed.categories.length).toBeGreaterThanOrEqual(3);
    expect(seed.categories.length).toBeLessThanOrEqual(4);
    const slugs = seed.categories.map((c) => c.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const s of slugs) expect(s).toMatch(/^[a-z0-9][a-z0-9-]*$/);
  });

  it("has 12 published products and one draft, each valid for its column", () => {
    expect(seed.products.filter((p) => p.status === "published")).toHaveLength(12);
    expect(seed.products.filter((p) => p.status === "draft")).toHaveLength(1);
    const slugs = new Set(seed.categories.map((c) => c.slug));
    for (const p of seed.products) {
      expect(PRODUCT_TYPES.has(p.product_type), p.name).toBe(true);
      expect(p.category_slug === null || slugs.has(p.category_slug), p.name).toBe(true);
      expect(p.name.length).toBeLessThanOrEqual(255);
      expect(p.price, p.name).toMatch(/^[0-9]{1,10}\.[0-9]{2}$/);
      expect(formatPrice(p.price), p.name).not.toBeNull();
      expect(typeof p.attributes === "object" && !Array.isArray(p.attributes)).toBe(true);
      for (const k of Object.keys(p.attributes)) expect(ATTRIBUTE_KEYS.has(k), `${p.name}: ${k}`).toBe(true);
      expect(Number.isInteger(p.sort_order)).toBe(true);
    }
    const published = seed.products.filter((p) => p.status === "published");
    for (const p of published) expect(p.images.length, p.name).toBeGreaterThan(0);
  });

  it("points every image at a bundled SVG with its exact byte size", () => {
    for (const p of seed.products) {
      for (const img of p.images) {
        expect(img.file).toMatch(/^products\/[a-z0-9-]+\.svg$/);
        const size = statSync(join(root, "public", img.file)).size;
        expect(img.byte_size, img.file).toBe(size);
        expect(img.byte_size).toBeGreaterThan(0);
        expect(img.alt_text?.length ?? 0, img.file).toBeGreaterThan(0);
        expect(imageSrc(`${WEBSITE}/${img.file}`)).toBe(`/${img.file}`);
      }
    }
  });

  it("states the storage the website needs, as seed/README.md does", () => {
    const sizes = seed.products.flatMap((p) => p.images.map((i) => i.byte_size));
    const readme = readFileSync(join(root, "seed", "README.md"), "utf8");
    const total = sizes.reduce((a, b) => a + b, 0).toLocaleString("en-US");
    const max = Math.max(...sizes).toLocaleString("en-US");
    expect(readme).toContain(`total **${total} bytes**`);
    expect(readme).toContain(`the largest is **${max} bytes**`);
  });
});
