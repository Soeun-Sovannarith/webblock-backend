// Mock session: this standalone portal has no sign-in, so the user is always the mock owner
// from src/mocks/session.ts. When Spring is ready, only these function bodies change.
import { notFound } from "next/navigation";
import { cache } from "react";
import { sessionUser, websiteOwners } from "@/mocks/session";
import type { SessionUser } from "./roles";

/** Signed-in user. */
export const requireUser = cache(async (): Promise<SessionUser> => sessionUser);

/** Signed-in owner of `websiteId`. Anyone else gets a 404 so ids are not leaked. */
export async function requireWebsiteOwner(websiteId: string): Promise<SessionUser> {
  const user = await requireUser();
  const owns = websiteOwners.some((w) => w.websiteId === websiteId && w.ownerId === user.id);
  if (!owns) notFound();
  return user;
}
