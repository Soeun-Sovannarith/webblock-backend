// The catalog outage as an HTTP answer. A page cannot choose its status in the App Router once it
// renders, so src/proxy.ts asks this gate first: when the catalog does not answer, the friendly page
// goes out as 503 with a Retry-After, never as 200 (a 200 outage page is indistinguishable from a
// shop without products). The page components still render <ShopUnavailable> for the rare read that
// fails after the gate passed.
import { isProductId } from "@/lib/catalog";
import { readinessResponse } from "@/lib/readiness";

export const OUTAGE_STATUS = 503;
const RETRY_AFTER_SECONDS = "10";

const OUTAGE_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Temporarily unavailable</title>
</head>
<body>
<main role="alert" style="font-family: system-ui, sans-serif; max-width: 36rem; margin: 4rem auto; padding: 0 1rem">
<p>Temporarily unavailable</p>
<h1>We&rsquo;ll be right back</h1>
<p>Our shop can&rsquo;t show its products at the moment. Nothing is wrong on your side. Please try again in a minute.</p>
</main>
</body>
</html>
`;

/** The friendly outage page, with status 503. */
export function outageResponse(): Response {
  return new Response(OUTAGE_HTML, {
    status: OUTAGE_STATUS,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Retry-After": RETRY_AFTER_SECONDS,
    },
  });
}

/** null when the catalog answers (serve the page); the 503 outage response otherwise. */
export async function outageGate(probe: () => Promise<boolean>, timeoutMs?: number): Promise<Response | null> {
  const readiness = await readinessResponse(probe, timeoutMs);
  return readiness.status === 200 ? null : outageResponse();
}

/** The paths the gate covers: the pages that read the catalog. Probes, pictures and a malformed
 * product ID (404 without a query) are not gated. */
export function gatesPath(pathname: string): boolean {
  if (pathname === "/") return true;
  const match = /^\/products\/([^/]+)$/.exec(pathname);
  return match !== null && isProductId(match[1] ?? "");
}
