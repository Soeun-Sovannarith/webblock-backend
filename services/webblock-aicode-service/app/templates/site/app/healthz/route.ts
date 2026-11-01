// Owned by the WebBlock harness and refreshed on every run. Kubernetes' process check (Provisioning's site
// contract): 200 whenever the server is up, without touching the database, so a database outage never takes the
// site out of its Service.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response("ok\n", {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}
