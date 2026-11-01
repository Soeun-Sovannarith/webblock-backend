// Data access for the website's products: the shared catalog's products and categories through lib/products.ts, the
// same rows the shop shows. Writes there check the owner's session again.
import {
  addProduct,
  DEFAULT_CATEGORY,
  deleteProduct as deleteRow,
  isId,
  listCategories,
  listPortalProducts,
  storageProblem,
  updateProduct as updateRow,
  type PortalProduct,
  type Photo,
} from "@/lib/products";
import { ApiError } from "@/portal/lib/api/errors";
import type { Page } from "@/portal/lib/api/types";
import { toPage, type TableQuery } from "@/portal/lib/table-query";
import type { Product, ProductCounts } from "./types";

/** "$38.00", "38" or "" → "38.00"; the portal edits prices as decimals ("0.00" when the price is hidden). */
function decimal(price: string): string {
  const value = Number(price.replace(/[^0-9.]/g, ""));
  return price.trim() && Number.isFinite(value) ? value.toFixed(2) : "0.00";
}

function toProduct(websiteId: string, row: PortalProduct): Product {
  return {
    id: row.id,
    websiteId,
    name: row.name,
    description: row.description ?? "",
    category: row.category,
    price: decimal(row.price),
    imageUrl: row.imageUrl,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}

async function allProducts(websiteId: string): Promise<Product[]> {
  return (await listPortalProducts()).map((row) => toProduct(websiteId, row));
}

function notFound(): never {
  throw new ApiError({ title: "Product not found", status: 404, detail: "That product no longer exists." });
}

/** A storage limit the database refused, as a 422 the form shows; anything else is rethrown. */
function storageError(error: unknown): never {
  const problem = storageProblem(error);
  if (problem) throw new ApiError({ title: "Photo not saved", status: 422, detail: problem });
  throw error;
}

export type ListProductsFilter = {
  q?: string;
  category?: string;
};

/** The website's products, filtered, sorted (newest first by default) and paged. */
export async function listProducts(
  websiteId: string,
  query: TableQuery,
  filter: ListProductsFilter = {},
): Promise<Page<Product>> {
  const q = filter.q?.trim().toLowerCase();
  const items = (await allProducts(websiteId))
    .filter((p) => !filter.category || p.category === filter.category)
    .filter((p) => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q));

  const sort = query.sort ?? { field: "createdAt", direction: "desc" as const };
  const sign = sort.direction === "asc" ? 1 : -1;
  items.sort((a, b) => {
    if (sort.field === "price") return sign * (Number(a.price) - Number(b.price));
    if (sort.field === "name") return sign * a.name.localeCompare(b.name);
    return sign * (a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
  });
  return toPage(items, query);
}

/** The website's categories, in catalog order, plus "General" so a site without products can add its first one. */
export async function listProductCategories(_websiteId: string): Promise<string[]> {
  const categories = new Set(await listCategories());
  categories.add(DEFAULT_CATEGORY);
  return [...categories];
}

export async function countProducts(websiteId: string): Promise<ProductCounts> {
  const [products, categories] = await Promise.all([allProducts(websiteId), listProductCategories(websiteId)]);
  return {
    total: products.length,
    categories: categories.map((category) => ({
      category,
      count: products.filter((p) => p.category === category).length,
    })),
  };
}

export async function getProduct(websiteId: string, productId: string): Promise<Product> {
  if (!isId(productId)) notFound();
  return (await allProducts(websiteId)).find((p) => p.id === productId) ?? notFound();
}

export type ProductBody = {
  name: string;
  category: string;
  description: string;
  /** Decimal string, e.g. "12.50". */
  price: string;
  /** A new photo; `undefined` keeps the current one on update. */
  photo?: Photo;
};

async function checkCategory(websiteId: string, category: string) {
  if (!(await listProductCategories(websiteId)).includes(category)) {
    throw new ApiError({ title: "Unknown category", status: 422, detail: "Choose one of your website’s categories." });
  }
}

export async function createProduct(websiteId: string, body: ProductBody): Promise<Product> {
  await checkCategory(websiteId, body.category);
  const { photo, ...product } = body;
  const id = await addProduct(product, photo).catch(storageError);
  return getProduct(websiteId, id);
}

export async function updateProduct(websiteId: string, productId: string, body: ProductBody): Promise<Product> {
  await checkCategory(websiteId, body.category);
  const { photo, ...product } = body;
  const updated = await updateRow(productId, product, photo).catch(storageError);
  if (!updated) notFound();
  return getProduct(websiteId, productId);
}

export async function deleteProduct(websiteId: string, productId: string): Promise<Product> {
  const product = await getProduct(websiteId, productId);
  if ((await deleteRow(productId)) === null) notFound();
  return product;
}
