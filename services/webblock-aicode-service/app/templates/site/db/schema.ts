// Owned by the WebBlock harness and refreshed on every run. The tables this site uses in WebBlock's shared catalog
// (schema "catalog", created by the harness's migrations; the site cannot create or change tables). The site logs in
// as its own database role, and row-level security shows it only its own website's rows: website_id is filled in by
// the database on insert (catalog.current_website_id()), so queries never need to name it. Images and the logo are
// files in object storage (lib/storage.ts); the rows keep only their storage key and size.
import { sql } from "drizzle-orm";
import { bigint, boolean, integer, jsonb, numeric, pgSchema, primaryKey, text, timestamp, uuid, varchar } from "drizzle-orm/pg-core";

export const catalog = pgSchema("catalog");

const websiteId = () => uuid("website_id").notNull().default(sql`catalog.current_website_id()`);
const createdAt = () => timestamp("created_at", { withTimezone: true }).defaultNow().notNull();
const updatedAt = () => timestamp("updated_at", { withTimezone: true }).defaultNow().notNull();

export const websiteStatus = catalog.enum("website_status", ["active", "suspended", "archived"]);
export const productStatus = catalog.enum("product_status", ["draft", "published", "archived"]);

// Reference data, read-only for sites.
export const businessTypes = catalog.table("business_types", {
  businessTypeId: uuid("business_type_id").primaryKey(),
  code: varchar("code", { length: 50 }).notNull(),
  name: varchar("name", { length: 100 }).notNull(),
  isActive: boolean("is_active").notNull(),
  createdAt: createdAt(),
});

export const productTypes = catalog.table("product_types", {
  productTypeId: uuid("product_type_id").primaryKey(),
  businessTypeId: uuid("business_type_id").notNull(),
  code: varchar("code", { length: 50 }).notNull(),
  name: varchar("name", { length: 100 }).notNull(),
  isActive: boolean("is_active").notNull(),
  createdAt: createdAt(),
});

export const productTypeSchemas = catalog.table("product_type_schemas", {
  productTypeSchemaId: uuid("product_type_schema_id").primaryKey(),
  productTypeId: uuid("product_type_id").notNull(),
  versionNumber: integer("version_number").notNull(),
  fieldSchema: jsonb("field_schema").notNull(),
  isActive: boolean("is_active").notNull(),
  createdAt: createdAt(),
});

// This site's own row (read-only): its status and storage limits.
export const websites = catalog.table("websites", {
  websiteId: uuid("website_id").primaryKey(),
  businessTypeId: uuid("business_type_id").notNull(),
  dbRoleName: varchar("db_role_name", { length: 63 }).notNull(),
  status: websiteStatus("status").notNull(),
  storageLimitBytes: bigint("storage_limit_bytes", { mode: "number" }).notNull(),
  maxImageBytes: bigint("max_image_bytes", { mode: "number" }).notNull(),
  storageUsedBytes: bigint("storage_used_bytes", { mode: "number" }).notNull(),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

// One row per website, created by the harness; the owner portal edits it (lib/business.ts). maps_url and
// portal_edited_at are WebBlock's additions to the design for the portal.
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
  openingHours: jsonb("opening_hours").$type<Array<{ day: string; isOpen: boolean; opens: string; closes: string }>>().notNull(),
  mapsUrl: text("maps_url"),
  portalEditedAt: timestamp("portal_edited_at", { withTimezone: true }),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

export const categories = catalog.table("categories", {
  categoryId: uuid("category_id").primaryKey().defaultRandom(),
  websiteId: websiteId(),
  name: varchar("name", { length: 100 }).notNull(),
  slug: varchar("slug", { length: 100 }).notNull(),
  sortOrder: integer("sort_order").default(0).notNull(),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

// The catalog the storefront shows and the owner portal edits (lib/products.ts). price is in USD; NULL hides it.
export const products = catalog.table("products", {
  productId: uuid("product_id").primaryKey().defaultRandom(),
  websiteId: websiteId(),
  productTypeSchemaId: uuid("product_type_schema_id").notNull().default(sql`catalog.item_schema_id(catalog.current_website_id())`),
  categoryId: uuid("category_id"),
  name: varchar("name", { length: 255 }).notNull(),
  description: text("description"),
  price: numeric("price", { precision: 12, scale: 2 }),
  attributes: jsonb("attributes").$type<Record<string, unknown>>().default({}).notNull(),
  status: productStatus("status").default("published").notNull(),
  sortOrder: integer("sort_order").default(0).notNull(),
  rowVersion: bigint("row_version", { mode: "number" }).default(1).notNull(),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

export const productImages = catalog.table("product_images", {
  productImageId: uuid("product_image_id").primaryKey().defaultRandom(),
  websiteId: websiteId(),
  productId: uuid("product_id").notNull(),
  storageKey: varchar("storage_key", { length: 500 }).notNull(),
  byteSize: bigint("byte_size", { mode: "number" }).notNull(),
  altText: varchar("alt_text", { length: 500 }),
  sortOrder: integer("sort_order").default(0).notNull(),
  createdAt: createdAt(),
});

// WebBlock's addition for the owner portal: sessions Keycloak ended (back-channel logout), refused by lib/portal-auth.ts.
export const portalRevokedSessions = catalog.table(
  "portal_revoked_sessions",
  {
    websiteId: websiteId(),
    sid: varchar("sid", { length: 255 }).notNull(),
    revokedAt: timestamp("revoked_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [primaryKey({ columns: [table.websiteId, table.sid] })],
);
