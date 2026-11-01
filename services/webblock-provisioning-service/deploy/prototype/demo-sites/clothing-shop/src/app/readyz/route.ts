// Readiness of the site with its dependencies (decision 0014 section 15): 200 only when a catalog
// query as the site's own role succeeds within a short timeout, 503 otherwise. The promotion probe
// uses it. /healthz stays the dependency-free process check.
import { catalogIsReady } from "@/lib/catalog";
import { readinessResponse } from "@/lib/readiness";

export const dynamic = "force-dynamic";

export function GET(): Promise<Response> {
  return readinessResponse(catalogIsReady);
}
