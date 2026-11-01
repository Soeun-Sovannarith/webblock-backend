// Owned by the WebBlock harness and refreshed on every run. Owner portal → Business information.
import { siteId } from "@/portal/config/site";
import { BusinessInfoView, getBusinessInfoView } from "@/portal/features/business-info";
import { requireWebsiteOwner } from "@/portal/lib/auth/session";
import type { SearchParams } from "@/portal/lib/table-query";

export const metadata = { title: "Business information" };

export default async function BusinessInfoPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const websiteId = siteId();
  const query = await searchParams;
  await requireWebsiteOwner(websiteId);
  const data = await getBusinessInfoView(websiteId, query);
  return <BusinessInfoView data={data} />;
}
