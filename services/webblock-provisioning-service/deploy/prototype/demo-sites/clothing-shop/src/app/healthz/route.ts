// Liveness and readiness of the web server alone. It never touches the catalog, so a database outage
// shows the friendly error page instead of taking the site out of its Service.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response("ok\n", {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}
