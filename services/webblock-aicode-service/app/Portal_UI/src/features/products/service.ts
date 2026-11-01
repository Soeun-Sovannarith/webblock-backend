// Product logic. Pages call the read functions, Server Actions call the write functions.
// Nothing here knows whether data comes from the mock or from Spring.
import { z } from "zod";
import type { ActionResult } from "@/lib/actions/result";
import { ApiError } from "@/lib/api/errors";
import type { Page } from "@/lib/api/types";
import { formFile, imageToDataUrl } from "@/lib/files";
import { checkImage, PRODUCT_IMAGE_RULE } from "@/lib/image-rules";
import { parseTableQuery, type SearchParams } from "@/lib/table-query";
import { formText } from "@/lib/validation/fields";
import { getWebsite } from "@/features/websites/api";
import {
  countProducts,
  createProduct,
  deleteProduct,
  listProductCategories,
  listProducts,
  updateProduct,
} from "./api";
import { productInput } from "./schemas";
import type { Product, ProductCounts } from "./types";

export type ProductsLayout = "grid" | "list";

export const PRODUCT_PAGE_SIZES = [6, 12, 24];
export const DEFAULT_PRODUCT_PAGE_SIZE = 6;

export type ProductsView = {
  websiteId: string;
  /** Public host shown in the page description, or `null` before the first deploy. */
  domain: string | null;
  products: Page<Product>;
  counts: ProductCounts;
  /** Every category of the website, in order, for the form. */
  categories: string[];
  category: string | null;
  q: string;
  layout: ProductsLayout;
};

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

export async function getProductsView(websiteId: string, searchParams: SearchParams): Promise<ProductsView> {
  const q = first(searchParams.q).trim();
  const layout: ProductsLayout = first(searchParams.view) === "list" ? "list" : "grid";
  const query = parseTableQuery(searchParams, { size: DEFAULT_PRODUCT_PAGE_SIZE });

  const [website, categories, counts] = await Promise.all([
    getWebsite(websiteId),
    listProductCategories(websiteId),
    countProducts(websiteId),
  ]);
  // Ignore a `?category=` the website does not have instead of showing an empty list.
  const requested = first(searchParams.category);
  const category = categories.includes(requested) ? requested : null;
  const products = await listProducts(websiteId, query, { q, category: category ?? undefined });

  return { websiteId, domain: website.domain, products, counts, categories, category, q, layout };
}

/** Creates a product, or updates `productId` when it is set. */
export async function saveProduct(
  websiteId: string,
  productId: string | null,
  formData: FormData,
): Promise<ActionResult<{ id: string }>> {
  const values = {
    name: formText(formData, "name"),
    category: formText(formData, "category"),
    description: formText(formData, "description"),
    price: formText(formData, "price"),
  };
  const parsed = productInput.safeParse(values);
  if (!parsed.success) {
    return {
      ok: false,
      message: "Please fix the highlighted fields.",
      fieldErrors: z.flattenError(parsed.error).fieldErrors,
      values,
    };
  }

  const image = formFile(formData, "image");
  let imageUrl: string | undefined;
  if (image) {
    const problem = checkImage(image, PRODUCT_IMAGE_RULE);
    if (problem) return { ok: false, message: problem, fieldErrors: { image: [problem] }, values };
    imageUrl = await imageToDataUrl(image);
  } else if (!productId) {
    return {
      ok: false,
      message: "Add a photo so customers can see the product.",
      fieldErrors: { image: ["Add a photo of the product."] },
      values,
    };
  }

  try {
    const product = productId
      ? await updateProduct(websiteId, productId, { ...parsed.data, imageUrl })
      : await createProduct(websiteId, { ...parsed.data, imageUrl });
    return {
      ok: true,
      data: { id: product.id },
      message: productId ? `${product.name} was updated` : `${product.name} was added to your products`,
    };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, message: e.message, values };
    throw e;
  }
}

export async function removeProduct(websiteId: string, productId: string): Promise<ActionResult> {
  try {
    const product = await deleteProduct(websiteId, productId);
    return { ok: true, data: undefined, message: `${product.name} was removed` };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, message: e.message };
    throw e;
  }
}
