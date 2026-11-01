// Data access for the signed-in user's profile. Mock now, Spring later: only these function bodies change.
import { ApiError } from "@/lib/api/errors";
import { delay } from "@/lib/api/delay";
import { profiles } from "@/mocks/account";
import type { Profile } from "./types";

export async function getProfile(userId: string): Promise<Profile> {
  await delay(120);
  const profile = profiles.find((p) => p.id === userId);
  if (!profile) {
    throw new ApiError({ title: "Account not found", status: 404, detail: "That account no longer exists." });
  }
  return profile;
}
