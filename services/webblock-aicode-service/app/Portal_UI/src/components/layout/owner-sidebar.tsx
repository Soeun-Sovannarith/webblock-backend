import { requireWebsiteOwner } from "@/lib/auth/session";
import { getProfile } from "@/features/account";
import { getWebsite } from "@/features/websites";
import { OwnerSidebarShell } from "./owner-sidebar-shell";

/** Owner Portal sidebar. Loads the website and the owner on the server, the shell handles clicks. */
export async function OwnerSidebar({ websiteId }: { websiteId: string }) {
  const user = await requireWebsiteOwner(websiteId);
  const [website, profile] = await Promise.all([getWebsite(websiteId), getProfile(user.id)]);
  return (
    <OwnerSidebarShell
      website={{ id: website.id, name: website.name, domain: website.domain }}
      profile={profile}
    />
  );
}
