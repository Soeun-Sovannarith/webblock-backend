// Owned by the WebBlock harness and refreshed on every run. Provisioning's promotion probe (site contract): 200 only
// when a database query as the site's own role answers within 2 s and its tenant token is known, 503 otherwise, so a
// release that cannot read its catalog never goes live. No redirect, no authentication, a tiny body. The log names the driver's error code only,
// never the driver's message, a host or a credential.
import { catalogIsReachable } from "@/lib/products";

export const dynamic = "force-dynamic";

const TIMEOUT_MS = 2_000;
const HEADERS = { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" };

// The driver's error code (e.g. 28P01 for a wrong password) when there is one: class names are minified in the build.
function describe(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" ? `code ${code}` : "no error code";
}

export async function GET(): Promise<Response> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(Object.assign(new Error("timed out"), { code: "TIMEOUT" })), TIMEOUT_MS);
  });
  let ready = false;
  try {
    await Promise.race([catalogIsReachable(), timeout]);
    ready = true;
  } catch (error) {
    console.error(`[webblock] readyz: not ready (${describe(error)})`);
  } finally {
    clearTimeout(timer);
  }
  return ready
    ? new Response("ready\n", { status: 200, headers: HEADERS })
    : new Response("not ready\n", { status: 503, headers: HEADERS });
}
