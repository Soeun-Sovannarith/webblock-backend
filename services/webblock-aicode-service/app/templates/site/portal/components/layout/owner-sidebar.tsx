import { requireWebsiteOwner } from "@/portal/lib/auth/session";
import { appLinks } from "@/portal/config/site";
import { getProfile } from "@/portal/features/account";
import { getWebsite } from "@/portal/features/websites";
import { OwnerSidebarShell } from "./owner-sidebar-shell";

/** Owner Portal sidebar. Loads the website, the owner and the WebBlock links on the server; the shell handles clicks. */
export async function OwnerSidebar({ websiteId }: { websiteId: string }) {
  const user = await requireWebsiteOwner(websiteId);
  const [website, profile] = await Promise.all([getWebsite(websiteId), getProfile(user.id)]);
  return (
    <OwnerSidebarShell
      website={{ id: website.id, name: website.name, domain: website.domain }}
      profile={profile}
      links={appLinks()}
    />
  );
}
