"use server";

import { redirect } from "next/navigation";
import { endSession } from "@/lib/portal-auth";

/** Signs out of the portal and of WebBlock single sign-on; Keycloak then returns to the site's home page. */
export async function signOutAction(): Promise<void> {
  redirect(await endSession());
}
