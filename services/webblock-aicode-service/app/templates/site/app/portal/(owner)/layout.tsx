// Owned by the WebBlock harness and refreshed on every run. The signed-in part of the owner portal: the sidebar and
// the screens. Each screen and each Server Action checks the owner again.
import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { portalEnabled } from "@/lib/portal-auth";
import { OwnerSidebar } from "@/portal/components/layout/owner-sidebar";
import { siteId } from "@/portal/config/site";
import { requireUser } from "@/portal/lib/auth/session";

export default async function OwnerLayout({ children }: { children: ReactNode }) {
  if (!portalEnabled()) redirect("/portal");
  await requireUser();
  return (
    <div className="flex min-h-dvh">
      <OwnerSidebar websiteId={siteId()} />
      <main className="page-motion flex min-w-0 flex-1 flex-col gap-6 px-page pt-8 pb-12">{children}</main>
    </div>
  );
}
