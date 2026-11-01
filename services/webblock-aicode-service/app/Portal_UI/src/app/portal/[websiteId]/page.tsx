import { redirect } from "next/navigation";

export default async function PortalPage({
  params,
}: {
  params: Promise<{ websiteId: string }>;
}) {
  const { websiteId } = await params;
  redirect(`/portal/${websiteId}/products`);
}
