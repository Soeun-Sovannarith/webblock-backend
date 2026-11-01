// Drizzle description of the shared catalog tables this shop reads, exactly as the platform database
// design (Notion "6 · Shared catalog") defines them: schema `catalog`, the same
// table and column names, types, nullability, keys and the `product_status` enum.
//
// The shop never migrates anything. The catalog owner (Project) owns the schema, its CHECK constraints,
// triggers, grants and row-level security; they are deliberately not repeated here. The shop logs in as
// its own `site_<uuid>` role, and row-level security limits every query to that website's rows, so no
// query in this shop filters by website. `catalog.websites` is not described because a site role cannot
// read it.
import {
  bigint,
  foreignKey,
  index,
  integer,
  jsonb,
  numeric,
  pgSchema,
  text,
  timestamp,
  unique,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";

export const catalog = pgSchema("catalog");

/** `catalog.product_status`: only `published` products are shown to visitors. */
export const productStatus = catalog.enum("product_status", ["draft", "published", "archived"]);

/** One opening-hours range of `business_profiles.opening_hours`. */
export type OpeningHoursRange = {
  from_day: string;
  to_day: string;
  opens_at: string;
  closes_at: string;
  closes_next_day: boolean;
};

/** `catalog.business_profiles`: one row per website, business details and contact information. */
export const businessProfiles = catalog.table("business_profiles", {
  websiteId: uuid("website_id").primaryKey(),
  businessName: varchar("business_name", { length: 255 }).notNull(),
  description: text("description"),
  logoStorageKey: varchar("logo_storage_key", { length: 500 }),
  logoByteSize: bigint("logo_byte_size", { mode: "number" }),
  phone: varchar("phone", { length: 50 }),
  email: varchar("email", { length: 320 }),
  address: text("address"),
  timezone: varchar("timezone", { length: 100 }).notNull(),
  openingHours: jsonb("opening_hours").$type<OpeningHoursRange[]>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull(),
});

/** `catalog.categories`: the website's own product groups. */
export const categories = catalog.table(
  "categories",
  {
    categoryId: uuid("category_id").primaryKey(),
    websiteId: uuid("website_id").notNull(),
    name: varchar("name", { length: 100 }).notNull(),
    slug: varchar("slug", { length: 100 }).notNull(),
    sortOrder: integer("sort_order").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull(),
  },
  (t) => [unique().on(t.websiteId, t.categoryId), unique().on(t.websiteId, t.slug)],
);

/** `catalog.products`: products shown on the website. `price` is USD; null means no price is shown. */
export const products = catalog.table(
  "products",
  {
    productId: uuid("product_id").primaryKey(),
    websiteId: uuid("website_id").notNull(),
    productTypeSchemaId: uuid("product_type_schema_id").notNull(),
    categoryId: uuid("category_id"),
    name: varchar("name", { length: 255 }).notNull(),
    description: text("description"),
    price: numeric("price", { precision: 12, scale: 2 }),
    attributes: jsonb("attributes").$type<Record<string, unknown>>().notNull(),
    status: productStatus("status").notNull(),
    sortOrder: integer("sort_order").notNull(),
    rowVersion: bigint("row_version", { mode: "bigint" }).notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull(),
  },
  (t) => [
    unique().on(t.websiteId, t.productId),
    // The design's ON DELETE SET NULL (category_id) names a column list, which Drizzle cannot express;
    // the database defines it, and the shop never deletes.
    foreignKey({
      columns: [t.websiteId, t.categoryId],
      foreignColumns: [categories.websiteId, categories.categoryId],
    }),
    index("products_listing").on(t.websiteId, t.status, t.sortOrder),
  ],
);

/** `catalog.product_images`: a product's pictures, by owned media object key, in gallery order. */
export const productImages = catalog.table(
  "product_images",
  {
    productImageId: uuid("product_image_id").primaryKey(),
    websiteId: uuid("website_id").notNull(),
    productId: uuid("product_id").notNull(),
    storageKey: varchar("storage_key", { length: 500 }).notNull(),
    byteSize: bigint("byte_size", { mode: "number" }).notNull(),
    altText: varchar("alt_text", { length: 500 }),
    sortOrder: integer("sort_order").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull(),
  },
  (t) => [
    foreignKey({
      columns: [t.websiteId, t.productId],
      foreignColumns: [products.websiteId, products.productId],
    }).onDelete("cascade"),
  ],
);
