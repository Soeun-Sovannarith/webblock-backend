// The signed-in owner's profile, from the single sign-on session (no photo or email: Keycloak's ID token is not kept).
import { requireUser } from "@/portal/lib/auth/session";
import { initials } from "@/portal/lib/format";
import type { Profile } from "./types";

export async function getProfile(userId: string): Promise<Profile> {
  const user = await requireUser();
  const fullName = user.id === userId ? user.fullName : "Owner";
  return { id: userId, fullName, initials: initials(fullName), email: user.email, avatarUrl: null };
}
