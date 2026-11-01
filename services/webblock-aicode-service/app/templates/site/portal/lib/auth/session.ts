// The signed-in owner, from the site's single sign-on session (lib/portal-auth.ts). Every page and every Server
// Action checks it itself, because an action can be called without loading the page.
import { notFound, redirect } from "next/navigation";
import { cache } from "react";
import { currentOwner } from "@/lib/portal-auth";
import { siteId } from "@/portal/config/site";
import type { SessionUser } from "./roles";

/** Signed-in owner; anyone else is sent to sign in. */
export const requireUser = cache(async (): Promise<SessionUser> => {
  const owner = await currentOwner();
  if (!owner) redirect("/portal/login");
  return { id: owner.sub, email: "", fullName: owner.name, role: "USER", avatarUrl: null };
});

/** Signed-in owner of this website. A site has one website, so any other id is a 404. */
export async function requireWebsiteOwner(websiteId: string): Promise<SessionUser> {
  const user = await requireUser();
  if (websiteId !== siteId()) notFound();
  return user;
}
