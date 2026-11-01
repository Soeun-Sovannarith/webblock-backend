// The Drizzle schema must describe the catalog tables exactly as Notion "6 · Shared catalog" does.
import { getTableConfig, type PgTable } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";

import { businessProfiles, categories, productImages, products, productStatus } from "@/db/schema";

type Col = [name: string, sqlType: string, notNull: boolean];

function columnsOf(table: PgTable): Col[] {
  return getTableConfig(table).columns.map((c) => [c.name, c.getSQLType(), c.notNull]);
}

describe("catalog schema", () => {
  it("lives in schema catalog with the design's table names", () => {
    expect(
      [businessProfiles, categories, products, productImages].map((t) => {
        const c = getTableConfig(t);
        return `${c.schema}.${c.name}`;
      }),
    ).toEqual(["catalog.business_profiles", "catalog.categories", "catalog.products", "catalog.product_images"]);
  });

  it("business_profiles", () => {
    expect(columnsOf(businessProfiles)).toEqual<Col[]>([
      ["website_id", "uuid", true],
      ["business_name", "varchar(255)", true],
      ["description", "text", false],
      ["logo_storage_key", "varchar(500)", false],
      ["logo_byte_size", "bigint", false],
      ["phone", "varchar(50)", false],
      ["email", "varchar(320)", false],
      ["address", "text", false],
      ["timezone", "varchar(100)", true],
      ["opening_hours", "jsonb", true],
      ["created_at", "timestamp with time zone", true],
      ["updated_at", "timestamp with time zone", true],
    ]);
  });

  it("categories", () => {
    expect(columnsOf(categories)).toEqual<Col[]>([
      ["category_id", "uuid", true],
      ["website_id", "uuid", true],
      ["name", "varchar(100)", true],
      ["slug", "varchar(100)", true],
      ["sort_order", "integer", true],
      ["created_at", "timestamp with time zone", true],
      ["updated_at", "timestamp with time zone", true],
    ]);
  });

  it("products", () => {
    expect(columnsOf(products)).toEqual<Col[]>([
      ["product_id", "uuid", true],
      ["website_id", "uuid", true],
      ["product_type_schema_id", "uuid", true],
      ["category_id", "uuid", false],
      ["name", "varchar(255)", true],
      ["description", "text", false],
      ["price", "numeric(12, 2)", false],
      ["attributes", "jsonb", true],
      ["status", "product_status", true],
      ["sort_order", "integer", true],
      ["row_version", "bigint", true],
      ["created_at", "timestamp with time zone", true],
      ["updated_at", "timestamp with time zone", true],
    ]);
    expect(getTableConfig(products).indexes.map((i) => i.config.name)).toEqual(["products_listing"]);
  });

  it("product_images", () => {
    expect(columnsOf(productImages)).toEqual<Col[]>([
      ["product_image_id", "uuid", true],
      ["website_id", "uuid", true],
      ["product_id", "uuid", true],
      ["storage_key", "varchar(500)", true],
      ["byte_size", "bigint", true],
      ["alt_text", "varchar(500)", false],
      ["sort_order", "integer", true],
      ["created_at", "timestamp with time zone", true],
    ]);
  });

  it("product_status has the design's values", () => {
    expect(productStatus.schema).toBe("catalog");
    expect(productStatus.enumName).toBe("product_status");
    expect(productStatus.enumValues).toEqual(["draft", "published", "archived"]);
  });
});
