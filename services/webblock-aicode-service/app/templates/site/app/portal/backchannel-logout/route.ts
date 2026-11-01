// Owned by the WebBlock harness and refreshed on every run. Keycloak calls this server-to-server when a user's
// session ends (sign-out anywhere in WebBlock, or an admin ending it); the portal session for it stops working.
import { handleBackchannelLogout, ssoConfig } from "@/lib/portal-auth";

export async function POST(request: Request) {
  const config = ssoConfig();
  if (!config) return new Response(null, { status: 404 });
  try {
    const form = await request.formData();
    const token = form.get("logout_token");
    if (typeof token !== "string") return new Response("logout_token is required", { status: 400 });
    await handleBackchannelLogout(config, token);
    return new Response(null, { status: 200, headers: { "cache-control": "no-store" } });
  } catch (error) {
    console.error("[webblock] Rejected a back-channel logout:", error);
    return new Response("invalid logout token", { status: 400, headers: { "cache-control": "no-store" } });
  }
}
