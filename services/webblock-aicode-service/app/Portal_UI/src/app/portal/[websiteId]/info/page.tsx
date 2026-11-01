import { requireWebsiteOwner } from "@/lib/auth/session";
import type { SearchParams } from "@/lib/table-query";
import { BusinessInfoView, getBusinessInfoView } from "@/features/business-info";

export const metadata = { title: "Business information" };

export default async function BusinessInfoPage({
  params,
  searchParams,
}: {
  params: Promise<{ websiteId: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { websiteId } = await params;
  const query = await searchParams;
  await requireWebsiteOwner(websiteId);
  const data = await getBusinessInfoView(websiteId, query);
  return <BusinessInfoView data={data} />;
}
