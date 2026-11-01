"use server";

import { revalidatePath } from "next/cache";
import { requireWebsiteOwner } from "@/lib/auth/session";
import type { ActionResult } from "@/lib/actions/result";
import { saveBusinessInfo } from "./service";

export async function updateBusinessInfoAction(
  websiteId: string,
  _prev: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  await requireWebsiteOwner(websiteId);
  const result = await saveBusinessInfo(websiteId, formData);
  if (result.ok) revalidatePath(`/portal/${websiteId}/info`);
  return result;
}
