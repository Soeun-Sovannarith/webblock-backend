// Owned by the WebBlock harness and refreshed on every run. Keycloak redirects here after sign-in.
import { NextResponse, type NextRequest } from "next/server";
import { completeLogin, siteOrigin, ssoConfig, type LoginResult } from "@/lib/portal-auth";

export async function GET(request: NextRequest) {
  const config = ssoConfig();
  if (!config) return NextResponse.redirect(new URL("/portal", await siteOrigin(null)));
  let result: LoginResult = "failed";
  try {
    if (!request.nextUrl.searchParams.has("error")) result = await completeLogin(config, request.nextUrl.searchParams);
  } catch (error) {
    console.error("[webblock] Portal sign-in failed:", error);
  }
  const target = result === "ok" ? "/portal" : `/portal?error=${result === "forbidden" ? "forbidden" : "signin"}`;
  return NextResponse.redirect(new URL(target, await siteOrigin(config)));
}
