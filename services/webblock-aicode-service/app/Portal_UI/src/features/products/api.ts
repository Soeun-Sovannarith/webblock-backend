// Data access for a website's products. Mock now, Spring later: only these function bodies change.
import { ApiError } from "@/lib/api/errors";
import { delay } from "@/lib/api/delay";
import type { Page } from "@/lib/api/types";
import { toPage, type TableQuery } from "@/lib/table-query";
import { productCategories, products as seed } from "@/mocks/products";
import type { Product, ProductCounts } from "./types";

const globalForStore = globalThis as typeof globalThis & { __wbProducts?: Product[] };
const store: Product[] = (globalForStore.__wbProducts ??= structuredClone(seed));

export type ListProductsFilter = {
  q?: string;
  category?: string;
};

/** GET /websites/{websiteId}/products?q=&category=&page=&size=&sort= — newest first by default. */
export async function listProducts(
  websiteId: string,
  query: TableQuery,
  filter: ListProductsFilter = {},
): Promise<Page<Product>> {
  await delay();
  const q = filter.q?.trim().toLowerCase();
  const items = store
    .filter((p) => p.websiteId === websiteId)
    .filter((p) => !filter.category || p.category === filter.category)
    .filter((p) => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q));

  const sort = query.sort ?? { field: "createdAt", direction: "desc" as const };
  const sign = sort.direction === "asc" ? 1 : -1;
  items.sort((a, b) => {
    if (sort.field === "price") return sign * (Number(a.price) - Number(b.price));
    if (sort.field === "name") return sign * a.name.localeCompare(b.name);
    return sign * a.createdAt.localeCompare(b.createdAt);
  });
  return toPage(items, query);
}

/** GET /websites/{websiteId}/products/counts */
export async function countProducts(websiteId: string): Promise<ProductCounts> {
  await delay(100);
  const mine = store.filter((p) => p.websiteId === websiteId);
  return {
    total: mine.length,
    categories: (productCategories[websiteId] ?? []).map((category) => ({
      category,
      count: mine.filter((p) => p.category === category).length,
    })),
  };
}

/** GET /websites/{websiteId}/product-categories */
export async function listProductCategories(websiteId: string): Promise<string[]> {
  await delay(100);
  return productCategories[websiteId] ?? [];
}

export async function getProduct(websiteId: string, productId: string): Promise<Product> {
  await delay(100);
  const product = store.find((p) => p.id === productId && p.websiteId === websiteId);
  if (!product) {
    throw new ApiError({ title: "Product not found", status: 404, detail: "That product no longer exists." });
  }
  return product;
}

export type ProductBody = {
  name: string;
  category: string;
  description: string;
  price: string;
  /** `undefined` keeps the current photo on update. */
  imageUrl?: string | null;
};

function checkCategory(websiteId: string, category: string) {
  if (!(productCategories[websiteId] ?? []).includes(category)) {
    throw new ApiError({ title: "Unknown category", status: 422, detail: "Choose one of your website’s categories." });
  }
}

/** POST /websites/{websiteId}/products */
export async function createProduct(websiteId: string, body: ProductBody): Promise<Product> {
  await delay(400);
  checkCategory(websiteId, body.category);
  const now = new Date().toISOString();
  const product: Product = {
    id: crypto.randomUUID(),
    websiteId,
    name: body.name,
    category: body.category,
    description: body.description,
    price: body.price,
    imageUrl: body.imageUrl ?? null,
    createdAt: now,
    updatedAt: now,
  };
  store.unshift(product);
  return product;
}

/** PUT /websites/{websiteId}/products/{productId} */
export async function updateProduct(websiteId: string, productId: string, body: ProductBody): Promise<Product> {
  const product = await getProduct(websiteId, productId);
  await delay(300);
  checkCategory(websiteId, body.category);
  Object.assign(product, {
    name: body.name,
    category: body.category,
    description: body.description,
    price: body.price,
    ...(body.imageUrl !== undefined && { imageUrl: body.imageUrl }),
    updatedAt: new Date().toISOString(),
  });
  return product;
}

/** DELETE /websites/{websiteId}/products/{productId} */
export async function deleteProduct(websiteId: string, productId: string): Promise<Product> {
  const product = await getProduct(websiteId, productId);
  await delay(300);
  store.splice(store.indexOf(product), 1);
  return product;
}
