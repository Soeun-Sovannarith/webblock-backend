"use server";

import { redirect } from "next/navigation";
import { appLinks } from "@/config/site";
import { requireUser } from "@/lib/auth/session";

/** No sign-in exists in this standalone portal, so logging out only leaves for the app's home page. */
export async function signOutAction(): Promise<void> {
  await requireUser();
  redirect(appLinks.home);
}
