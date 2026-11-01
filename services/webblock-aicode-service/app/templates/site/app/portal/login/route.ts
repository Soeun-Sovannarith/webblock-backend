// Owned by the WebBlock harness and refreshed on every run. Starts portal sign-in: redirects to Keycloak, which
// sends an owner already signed in to WebBlock straight back to /portal/callback.
import { NextResponse, type NextRequest } from "next/server";
import { beginLogin, siteOrigin, ssoConfig } from "@/lib/portal-auth";

export async function GET(request: NextRequest) {
  const config = ssoConfig();
  if (!config) return NextResponse.redirect(new URL("/portal", await siteOrigin(null)));
  try {
    return NextResponse.redirect(await beginLogin(config, { forceLogin: request.nextUrl.searchParams.get("prompt") === "login" }));
  } catch (error) {
    console.error("[webblock] Portal sign-in could not start:", error);
    return NextResponse.redirect(new URL("/portal?error=signin", await siteOrigin(config)));
  }
}
