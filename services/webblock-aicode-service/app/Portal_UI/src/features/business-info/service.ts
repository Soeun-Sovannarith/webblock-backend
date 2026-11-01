// Business details logic. Pages call the read functions, Server Actions call the write functions.
// Nothing here knows whether data comes from the mock or from Spring.
import { z } from "zod";
import type { ActionResult } from "@/lib/actions/result";
import { ApiError } from "@/lib/api/errors";
import { formFile, imageToDataUrl } from "@/lib/files";
import { checkImage, LOGO_RULE } from "@/lib/image-rules";
import type { SearchParams } from "@/lib/table-query";
import { formText } from "@/lib/validation/fields";
import { getWebsite } from "@/features/websites/api";
import type { Website } from "@/features/websites/types";
import { getBusinessInfo, updateBusinessInfo } from "./api";
import { businessInfoInput } from "./schemas";
import type { BusinessInfo } from "./types";

export type BusinessInfoView = {
  info: BusinessInfo;
  website: Pick<Website, "id" | "name" | "businessType" | "domain" | "thumbnailUrl">;
  /** `?edit=1`: the page shows the form instead of the details. */
  editing: boolean;
};

export async function getBusinessInfoView(websiteId: string, searchParams: SearchParams): Promise<BusinessInfoView> {
  const [info, website] = await Promise.all([getBusinessInfo(websiteId), getWebsite(websiteId)]);
  const { id, name, businessType, domain, thumbnailUrl } = website;
  return {
    info,
    website: { id, name, businessType, domain, thumbnailUrl },
    editing: searchParams.edit === "1",
  };
}

export async function saveBusinessInfo(websiteId: string, formData: FormData): Promise<ActionResult> {
  const values = {
    description: formText(formData, "description"),
    phone: formText(formData, "phone"),
    email: formText(formData, "email"),
    address: formText(formData, "address"),
    mapsUrl: formText(formData, "mapsUrl"),
    hours: formText(formData, "hours"),
  };
  const parsed = businessInfoInput.safeParse(values);
  if (!parsed.success) {
    return {
      ok: false,
      message: "Please fix the highlighted fields.",
      fieldErrors: z.flattenError(parsed.error).fieldErrors,
      values,
    };
  }

  const logo = formFile(formData, "logo");
  let logoUrl: string | undefined;
  if (logo) {
    const problem = checkImage(logo, LOGO_RULE);
    if (problem) return { ok: false, message: problem, fieldErrors: { logo: [problem] }, values };
    logoUrl = await imageToDataUrl(logo);
  }

  try {
    await updateBusinessInfo(websiteId, { ...parsed.data, logoUrl });
    return { ok: true, data: undefined, message: "Your changes go live on your next deploy." };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, message: e.message, values };
    throw e;
  }
}
