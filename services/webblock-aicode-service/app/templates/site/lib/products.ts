// Owned by the WebBlock harness and refreshed on every run. The product catalog in WebBlock's shared catalog (the
// "products", "categories" and "product_images" tables in db/schema.ts): read by the storefront route, written by the
// owner portal (/portal). Row-level security limits every query to this website's rows (lib/db.ts). Photos are files in
// object storage (lib/storage.ts), served at /media/products/<id>.
import type postgres from "postgres";
import { currentWebsiteId, getSql } from "./db";
import { isSignedIn } from "./portal-auth";
import { deleteFile, fileKey, getFile, putFile, type StoredFile } from "./storage";

export type CatalogProduct = {
  id: string;
  name: string;
  /** As the shop shows it: "$12.50", or "" when the price is hidden. */
  price: string;
  description: string | null;
  category: string;
  /** /media/products/<id> for an uploaded photo, or null. */
  imageUrl: string | null;
  /** Published (shown to visitors); drafts and archived products are hidden. */
  isAvailable: boolean;
};

export type PortalProduct = CatalogProduct & { createdAt: string; updatedAt: string };

export type ProductInput = {
  name: string;
  /** Decimal USD, e.g. "12.50"; "" hides the price. */
  price: string;
  description: string | null;
  category: string;
};

/** An uploaded image, before it goes to object storage. */
export type Photo = { contentType: string; bytes: Buffer };

/** Products and photos can only belong to this site's categories and products, so any other id is simply not found. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isId = (id: string) => UUID.test(id);

export const DEFAULT_CATEGORY = "General";

// The detail page path segment for a product name. Keep in sync with productSlug in
// components/blocks/ProductCards.tsx, which links each card to /products/<slug>.
export function productSlug(name: string) {
  const slug = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (slug) return slug;
  let hash = 5381;
  for (const ch of name) hash = (hash * 33 + ch.codePointAt(0)!) >>> 0;
  return "item-" + hash.toString(36);
}

/** catalog.categories.slug for a category name. Keep in sync with category_slug in the harness's seed_generator.py. */
export function categorySlug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100).replace(/-+$/, "") || "general";
}

type Row = Omit<PortalProduct, "createdAt" | "updatedAt"> & { createdAt: Date; updatedAt: Date };

function selectProducts(sql: postgres.Sql, includeHidden: boolean) {
  return sql<Row[]>`
    SELECT p.product_id AS id, p.name, CASE WHEN p.price IS NULL THEN '' ELSE '$' || p.price::text END AS price,
           p.description, COALESCE(c.name, ${DEFAULT_CATEGORY}) AS category,
           (SELECT '/media/products/' || p.product_id || '?v=' || i.product_image_id FROM product_images i
            WHERE i.product_id = p.product_id ORDER BY i.sort_order, i.created_at LIMIT 1) AS "imageUrl",
           p.status = 'published' AS "isAvailable", p.created_at AS "createdAt", p.updated_at AS "updatedAt"
    FROM products p LEFT JOIN categories c ON c.category_id = p.category_id
    WHERE ${includeHidden} OR p.status = 'published'
    ORDER BY p.sort_order, p.created_at`;
}

/** The catalog, in display order; null when there is no database or the query fails. */
export async function listProducts({ includeHidden = false } = {}): Promise<CatalogProduct[] | null> {
  const sql = getSql();
  if (!sql) return null;
  try {
    return (await selectProducts(sql, includeHidden)).map(({ createdAt: _c, updatedAt: _u, ...product }) => product);
  } catch (error) {
    console.error("[webblock] Could not load products:", error);
    return null;
  }
}

/** Every product with when it was added and last edited, for the owner portal. Throws when the database fails. */
export async function listPortalProducts(): Promise<PortalProduct[]> {
  const sql = getSql();
  if (!sql) return [];
  const rows = await selectProducts(sql, true);
  return rows.map((row) => ({ ...row, createdAt: row.createdAt.toISOString(), updatedAt: row.updatedAt.toISOString() }));
}

/** The website's categories, in order. */
export async function listCategories(): Promise<string[]> {
  const sql = getSql();
  if (!sql) return [];
  const rows = await sql<{ name: string }[]>`SELECT name FROM categories ORDER BY sort_order, name`;
  return rows.map((row) => row.name);
}

/** The photo of a product, or null when it has none. Public, like the shop that shows it. */
export async function productPhoto(id: string): Promise<StoredFile | null> {
  const sql = getSql();
  if (!sql || !isId(id)) return null;
  const [row] = await sql<{ key: string }[]>`
    SELECT storage_key AS key FROM product_images WHERE product_id = ${id} ORDER BY sort_order, created_at LIMIT 1`;
  return row ? getFile(row.key) : null;
}

/** /readyz: the products table answers through the site's own connection (rows or not), and the database maps the
 * site's role to an active website. Throws on failure. */
export async function catalogIsReachable(): Promise<void> {
  const sql = getSql();
  if (!sql) throw Object.assign(new Error("no database settings"), { code: "NO_DATABASE_SETTINGS" });
  await sql`SELECT 1 FROM products LIMIT 1`;
  const [row] = await sql<{ known: boolean }[]>`SELECT current_website_id() IS NOT NULL AS known`;
  if (!row?.known) throw Object.assign(new Error("no active website for this role"), { code: "UNKNOWN_WEBSITE" });
}

/** A storage limit the database refused (an image over max_image_bytes, or the website's storage full), as a sentence
 * for the owner; null for any other error. */
export function storageProblem(error: unknown): string | null {
  const e = error as { code?: string; constraint_name?: string } | null;
  if (e?.code !== "23514") return null;
  if (e.constraint_name === "ck_websites_max_image_bytes") return "This image is larger than your website allows.";
  if (e.constraint_name === "ck_websites_storage_within_limit") return "Your website's storage is full. Remove some photos first.";
  return null;
}

// Writes check the owner's session themselves, so code added to the site later (for example a new route
// handler) cannot change the catalog without a signed-in owner.
async function ownerSql() {
  if (!(await isSignedIn())) throw new Error("Only the signed-in owner can change products.");
  const sql = getSql();
  if (!sql) throw new Error("The site has no database settings.");
  return sql;
}

async function categoryId(sql: postgres.Sql, name: string): Promise<string> {
  const [row] = await sql<{ id: string }[]>`
    INSERT INTO categories (name, slug, sort_order)
    VALUES (${name}, ${categorySlug(name)}, (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories))
    ON CONFLICT (website_id, slug) DO UPDATE SET name = categories.name
    RETURNING category_id AS id`;
  return row.id;
}

/** Uploads the photo, then makes it the product's only image; the replaced files are deleted afterwards. */
async function replacePhoto(sql: postgres.Sql, productId: string, photo: Photo) {
  const websiteId = await currentWebsiteId();
  if (!websiteId) throw new Error("This website has no active catalog.");
  const key = fileKey(websiteId, `products/${productId}`, photo.contentType);
  await putFile(key, photo.bytes, photo.contentType);
  let replaced: { key: string }[];
  try {
    // One transaction: the old rows' bytes are released before the new one is counted against the storage limit.
    replaced = await sql.begin(async (tx) => {
      const old = await tx<{ key: string }[]>`DELETE FROM product_images WHERE product_id = ${productId} RETURNING storage_key AS key`;
      await tx`INSERT INTO product_images (product_id, storage_key, byte_size) VALUES (${productId}, ${key}, ${photo.bytes.length})`;
      return old;
    });
  } catch (error) {
    await deleteFile(key);
    throw error;
  }
  await Promise.all(replaced.map((row) => deleteFile(row.key)));
}

/** Adds a product at the end of the catalog, with its photo when given, and returns its id. */
export async function addProduct(product: ProductInput, photo?: Photo): Promise<string> {
  const sql = await ownerSql();
  const category = await categoryId(sql, product.category);
  const [row] = await sql<{ id: string }[]>`
    INSERT INTO products (category_id, name, description, price, sort_order)
    VALUES (${category}, ${product.name}, ${product.description}, ${product.price || null},
            (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM products))
    RETURNING product_id AS id`;
  if (photo) {
    try {
      await replacePhoto(sql, row.id, photo);
    } catch (error) {
      await sql`DELETE FROM products WHERE product_id = ${row.id}`;
      throw error;
    }
  }
  return row.id;
}

/** Changes a product's details, and its photo when one is given. Returns false when the product is gone. */
export async function updateProduct(id: string, product: ProductInput, photo?: Photo): Promise<boolean> {
  if (!isId(id)) return false;
  const sql = await ownerSql();
  const category = await categoryId(sql, product.category);
  const rows = await sql`
    UPDATE products SET name = ${product.name}, price = ${product.price || null}, description = ${product.description},
           category_id = ${category}
    WHERE product_id = ${id} RETURNING product_id`;
  if (rows.length === 0) return false;
  if (photo) await replacePhoto(sql, id, photo);
  return true;
}

export async function setProductAvailable(id: string, isAvailable: boolean) {
  if (!isId(id)) return;
  const sql = await ownerSql();
  await sql`UPDATE products SET status = ${isAvailable ? "published" : "draft"} WHERE product_id = ${id}`;
}

/** Deletes a product and its photos; returns its name, or null when it was already gone. */
export async function deleteProduct(id: string): Promise<string | null> {
  if (!isId(id)) return null;
  const sql = await ownerSql();
  const images = await sql<{ key: string }[]>`SELECT storage_key AS key FROM product_images WHERE product_id = ${id}`;
  const [row] = await sql<{ name: string }[]>`DELETE FROM products WHERE product_id = ${id} RETURNING name`;
  if (row) await Promise.all(images.map((image) => deleteFile(image.key)));
  return row?.name ?? null;
}
