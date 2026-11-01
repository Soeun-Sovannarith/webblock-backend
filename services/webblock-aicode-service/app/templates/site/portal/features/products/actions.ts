"use server";

import { revalidatePath } from "next/cache";
import { requireWebsiteOwner } from "@/portal/lib/auth/session";
import type { ActionResult } from "@/portal/lib/actions/result";
import { removeProduct, saveProduct } from "./service";

export async function createProductAction(
  websiteId: string,
  _prev: ActionResult<{ id: string }> | null,
  formData: FormData,
): Promise<ActionResult<{ id: string }>> {
  await requireWebsiteOwner(websiteId);
  const result = await saveProduct(websiteId, null, formData);
  if (result.ok) revalidatePath("/portal/products");
  return result;
}

export async function updateProductAction(
  websiteId: string,
  productId: string,
  _prev: ActionResult<{ id: string }> | null,
  formData: FormData,
): Promise<ActionResult<{ id: string }>> {
  await requireWebsiteOwner(websiteId);
  const result = await saveProduct(websiteId, productId, formData);
  if (result.ok) revalidatePath("/portal/products");
  return result;
}

export async function deleteProductAction(websiteId: string, productId: string): Promise<ActionResult> {
  await requireWebsiteOwner(websiteId);
  const result = await removeProduct(websiteId, productId);
  if (result.ok) revalidatePath("/portal/products");
  return result;
}
