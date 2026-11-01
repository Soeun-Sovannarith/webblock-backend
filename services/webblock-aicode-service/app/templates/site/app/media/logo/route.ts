// Owned by the WebBlock harness and refreshed on every run. The business logo uploaded in the owner portal, stored in
// object storage (lib/storage.ts) and referenced by catalog.business_profiles (lib/business.ts). Public.
import { businessLogo } from "@/lib/business";
import { fileResponse } from "@/lib/media";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return fileResponse(await businessLogo());
  } catch (error) {
    console.error("[webblock] Could not load the logo:", (error as { code?: string }).code ?? "error");
    return new Response(null, { status: 503 });
  }
}
