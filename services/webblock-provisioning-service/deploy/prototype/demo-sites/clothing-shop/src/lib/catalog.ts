// The shop's read model: every catalog read goes through these functions, written with Drizzle's
// query builder so each value is a bound parameter.
//
// Tenancy: the shop logs in as its website's own `site_<uuid>` role and the database's row-level
// security returns only that website's rows. No query here filters by website, and none reads
// `catalog.websites`. Visibility: visitors see only `published` products, which this module enforces
// (the design leaves published-only reads to the site's code).
import { asc, eq, inArray, and } from "drizzle-orm";
import { cache } from "react";

import { catalogDb } from "@/db/client";
import { CatalogConfigError } from "@/db/config";
import {
  businessProfiles,
  categories,
  productImages,
  products,
  type OpeningHoursRange,
} from "@/db/schema";
import { imageSrc } from "@/lib/images";

/** The catalog could not be read. Carries no driver text, host or credential. */
export class CatalogUnavailableError extends Error {
  constructor() {
    super("The catalog is unavailable");
    this.name = "CatalogUnavailableError";
  }
}

export type ShopProfile = {
  name: string;
  tagline: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  openingHours: OpeningHoursRange[];
};

export type ProductImage = { src: string | null; alt: string };

export type ProductSummary = {
  id: string;
  name: string;
  categoryName: string | null;
  price: string | null;
  image: ProductImage | null;
};

export type ProductDetail = ProductSummary & {
  description: string | null;
  categorySlug: string | null;
  attributes: Record<string, unknown>;
  images: ProductImage[];
};

export type Category = { slug: string; name: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,99}$/;
/** Upper bound of one listing; a website has at most a few thousand products (design section 6). */
const LISTING_LIMIT = 500;

/** True when `id` can be a product ID. Anything else is answered with "not found", not a query. */
export function isProductId(id: string): boolean {
  return UUID_PATTERN.test(id);
}

/** True when `slug` can be a category slug. */
export function isCategorySlug(slug: string): boolean {
  return SLUG_PATTERN.test(slug);
}

// Logs what failed by error class and driver code only, then hides the cause from callers and pages.
async function read<T>(what: string, query: () => Promise<T>): Promise<T> {
  try {
    return await query();
  } catch (err) {
    if (err instanceof CatalogConfigError) {
      // Names the missing or invalid keys, never a value.
      console.error(`catalog: ${what} failed: ${err.message}`);
      throw new CatalogUnavailableError();
    }
    const e = err as { name?: unknown; code?: unknown; cause?: { code?: unknown } };
    const code = e.code ?? e.cause?.code;
    console.error(
      `catalog: ${what} failed (${typeof e.name === "string" ? e.name : "Error"}${typeof code === "string" ? ` ${code}` : ""})`,
    );
    throw new CatalogUnavailableError();
  }
}

/**
 * Runs catalog reads and returns their result, or null when the catalog is unavailable, so a page can
 * render its friendly outage page on the server. Any other error is passed on.
 */
export async function unlessUnavailable<T>(load: () => Promise<T>): Promise<T | null> {
  try {
    return await load();
  } catch (err) {
    if (err instanceof CatalogUnavailableError) return null;
    throw err;
  }
}

function altFor(productName: string, altText: string | null): string {
  return altText !== null && altText.trim() !== "" ? altText : productName;
}

/** The website's business profile, or null if it has none yet. Cached per request. */
export const loadProfile = cache(async (): Promise<ShopProfile | null> => {
  const rows = await read("profile", () =>
    catalogDb()
      .select({
        name: businessProfiles.businessName,
        tagline: businessProfiles.description,
        phone: businessProfiles.phone,
        email: businessProfiles.email,
        address: businessProfiles.address,
        openingHours: businessProfiles.openingHours,
      })
      .from(businessProfiles)
      .limit(1),
  );
  const row = rows[0];
  if (row === undefined) return null;
  return { ...row, openingHours: Array.isArray(row.openingHours) ? row.openingHours : [] };
});

/** The website's categories that have at least one published product, in display order. */
export async function loadCategories(): Promise<Category[]> {
  return read("categories", () =>
    catalogDb()
      .selectDistinct({ slug: categories.slug, name: categories.name, sortOrder: categories.sortOrder })
      .from(categories)
      .innerJoin(products, eq(products.categoryId, categories.categoryId))
      .where(eq(products.status, "published"))
      .orderBy(asc(categories.sortOrder), asc(categories.name))
      .then((rows) => rows.map(({ slug, name }) => ({ slug, name }))),
  );
}

/** Published products, optionally of one category, each with its first image. */
export async function loadProducts(categorySlug?: string): Promise<ProductSummary[]> {
  const rows = await read("products", () =>
    catalogDb()
      .select({
        id: products.productId,
        name: products.name,
        price: products.price,
        categoryName: categories.name,
      })
      .from(products)
      .leftJoin(categories, eq(categories.categoryId, products.categoryId))
      .where(
        categorySlug === undefined
          ? eq(products.status, "published")
          : and(eq(products.status, "published"), eq(categories.slug, categorySlug)),
      )
      .orderBy(asc(products.sortOrder), asc(products.name))
      .limit(LISTING_LIMIT),
  );
  if (rows.length === 0) return [];

  const imageRows = await read("product images", () =>
    catalogDb()
      .select({
        productId: productImages.productId,
        storageKey: productImages.storageKey,
        altText: productImages.altText,
      })
      .from(productImages)
      .where(
        inArray(
          productImages.productId,
          rows.map((r) => r.id),
        ),
      )
      .orderBy(asc(productImages.productId), asc(productImages.sortOrder)),
  );
  const firstImage = new Map<string, { storageKey: string; altText: string | null }>();
  for (const img of imageRows) {
    if (!firstImage.has(img.productId)) firstImage.set(img.productId, img);
  }

  return rows.map((r) => {
    const img = firstImage.get(r.id);
    return {
      ...r,
      image: img === undefined ? null : { src: imageSrc(img.storageKey), alt: altFor(r.name, img.altText) },
    };
  });
}

/**
 * One published product with all its images, or null when there is no such published product. Cached
 * per request, so the page and its metadata share one read.
 */
export const loadProduct = cache(async (id: string): Promise<ProductDetail | null> => {
  if (!isProductId(id)) return null;
  const rows = await read("product", () =>
    catalogDb()
      .select({
        id: products.productId,
        name: products.name,
        description: products.description,
        price: products.price,
        attributes: products.attributes,
        categoryName: categories.name,
        categorySlug: categories.slug,
      })
      .from(products)
      .leftJoin(categories, eq(categories.categoryId, products.categoryId))
      .where(and(eq(products.productId, id), eq(products.status, "published")))
      .limit(1),
  );
  const row = rows[0];
  if (row === undefined) return null;

  const imageRows = await read("product images", () =>
    catalogDb()
      .select({ storageKey: productImages.storageKey, altText: productImages.altText })
      .from(productImages)
      .where(eq(productImages.productId, row.id))
      .orderBy(asc(productImages.sortOrder)),
  );
  const images = imageRows.map((img) => ({
    src: imageSrc(img.storageKey),
    alt: altFor(row.name, img.altText),
  }));
  return { ...row, image: images[0] ?? null, images };
});

/**
 * The /readyz probe: true when exactly one business profile is visible, which needs a working
 * connection, valid credentials and a role that row-level security maps to an active website. Errors
 * are passed on unlogged; the caller logs them without their text.
 */
export async function catalogIsReady(): Promise<boolean> {
  return (await catalogDb().$count(businessProfiles)) === 1;
}
