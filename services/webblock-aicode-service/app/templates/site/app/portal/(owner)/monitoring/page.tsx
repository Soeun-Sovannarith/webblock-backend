// Owned by the WebBlock harness and refreshed on every run. Owner portal → Monitor (what the site can check itself).
import { siteId } from "@/portal/config/site";
import { getMonitorView, MonitorView } from "@/portal/features/monitoring";
import { requireWebsiteOwner } from "@/portal/lib/auth/session";

export const metadata = { title: "Monitor" };

export default async function MonitoringPage() {
  const websiteId = siteId();
  await requireWebsiteOwner(websiteId);
  const data = await getMonitorView(websiteId);
  return <MonitorView data={data} />;
}
