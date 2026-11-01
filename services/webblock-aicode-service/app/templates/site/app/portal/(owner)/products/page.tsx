// Owned by the WebBlock harness and refreshed on every run. Owner portal → Products (the shop's catalog).
import { siteId } from "@/portal/config/site";
import { getProductsView, ProductsView } from "@/portal/features/products";
import { requireWebsiteOwner } from "@/portal/lib/auth/session";
import type { SearchParams } from "@/portal/lib/table-query";

export const metadata = { title: "Products" };

export default async function ProductsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const websiteId = siteId();
  const query = await searchParams;
  await requireWebsiteOwner(websiteId);
  const data = await getProductsView(websiteId, query);
  return <ProductsView data={data} searchParams={query} />;
}
