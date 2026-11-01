// Business details logic. Pages call the read functions, Server Actions call the write functions.
import { z } from "zod";
import type { ActionResult } from "@/portal/lib/actions/result";
import { ApiError } from "@/portal/lib/api/errors";
import { storageConfigured } from "@/lib/storage";
import { formFile, imageToPhoto } from "@/portal/lib/files";
import { checkImage, LOGO_RULE } from "@/portal/lib/image-rules";
import type { SearchParams } from "@/portal/lib/table-query";
import { formText } from "@/portal/lib/validation/fields";
import { getWebsite } from "@/portal/features/websites/api";
import type { Website } from "@/portal/features/websites/types";
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

  const logoFile = formFile(formData, "logo");
  let logo: Awaited<ReturnType<typeof imageToPhoto>> | undefined;
  if (logoFile) {
    const problem = !storageConfigured() ? "Logo uploads aren’t set up for this website yet." : checkImage(logoFile, LOGO_RULE);
    if (problem) return { ok: false, message: problem, fieldErrors: { logo: [problem] }, values };
    logo = await imageToPhoto(logoFile);
  }

  try {
    await updateBusinessInfo(websiteId, { ...parsed.data, logo });
    return { ok: true, data: undefined, message: "Your changes are live on your website." };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, message: e.message, values };
    throw e;
  }
}
