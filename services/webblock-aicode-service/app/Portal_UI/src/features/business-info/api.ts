// Data access for a website's business details. Mock now, Spring later: only these function bodies change.
import { ApiError } from "@/lib/api/errors";
import { delay } from "@/lib/api/delay";
import { businessInfo as seed } from "@/mocks/business-info";
import type { BusinessInfo } from "./types";

const globalForStore = globalThis as typeof globalThis & { __wbBusinessInfo?: BusinessInfo[] };
const store: BusinessInfo[] = (globalForStore.__wbBusinessInfo ??= structuredClone(seed));

/** GET /websites/{websiteId}/business-info */
export async function getBusinessInfo(websiteId: string): Promise<BusinessInfo> {
  await delay();
  const info = store.find((b) => b.websiteId === websiteId);
  if (!info) {
    throw new ApiError({
      title: "Business details not found",
      status: 404,
      detail: "This website has no business details yet.",
    });
  }
  return info;
}

export type BusinessInfoBody = Pick<BusinessInfo, "description" | "phone" | "email" | "address" | "mapsUrl" | "hours"> & {
  /** `undefined` keeps the current logo. */
  logoUrl?: string | null;
};

/** PUT /websites/{websiteId}/business-info */
export async function updateBusinessInfo(websiteId: string, body: BusinessInfoBody): Promise<BusinessInfo> {
  const info = await getBusinessInfo(websiteId);
  await delay(300);
  Object.assign(info, {
    description: body.description,
    phone: body.phone,
    email: body.email,
    address: body.address,
    mapsUrl: body.mapsUrl,
    hours: body.hours,
    ...(body.logoUrl !== undefined && { logoUrl: body.logoUrl }),
    updatedAt: new Date().toISOString(),
  });
  return info;
}
