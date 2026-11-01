import { requireWebsiteOwner } from "@/lib/auth/session";
import type { SearchParams } from "@/lib/table-query";
import { getProductsView, ProductsView } from "@/features/products";

export const metadata = { title: "Products" };

export default async function ProductsPage({
  params,
  searchParams,
}: {
  params: Promise<{ websiteId: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { websiteId } = await params;
  const query = await searchParams;
  await requireWebsiteOwner(websiteId);
  const data = await getProductsView(websiteId, query);
  return <ProductsView data={data} searchParams={query} />;
}
