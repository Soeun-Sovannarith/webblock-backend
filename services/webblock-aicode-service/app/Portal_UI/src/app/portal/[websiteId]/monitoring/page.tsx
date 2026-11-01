import { requireWebsiteOwner } from "@/lib/auth/session";
import { getMonitorView, MonitorView } from "@/features/monitoring";

export const metadata = { title: "Monitor" };

export default async function MonitoringPage({
  params,
}: {
  params: Promise<{ websiteId: string }>;
}) {
  const { websiteId } = await params;
  await requireWebsiteOwner(websiteId);
  const data = await getMonitorView(websiteId);
  return <MonitorView data={data} websiteId={websiteId} />;
}
