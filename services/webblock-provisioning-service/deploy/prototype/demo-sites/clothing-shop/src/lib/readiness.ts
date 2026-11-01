// The /readyz decision (decision 0014 section 15), kept apart from the database so it can be tested
// with a fake probe. 200 only when the probe confirms the catalog within the timeout; 503 otherwise.
// The log names the error class and driver code only, never its message, a host or a credential.

/** How long /readyz waits for the catalog query before answering 503. */
export const READINESS_TIMEOUT_MS = 2_000;

const HEADERS = { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" };

class ReadinessTimeout extends Error {
  constructor() {
    super("timed out");
    this.name = "ReadinessTimeout";
  }
}

function describeFailure(err: unknown): string {
  const e = err as { name?: unknown; code?: unknown; cause?: { code?: unknown } } | null;
  const name = typeof e?.name === "string" ? e.name : "Error";
  const code = e?.code ?? e?.cause?.code;
  return typeof code === "string" ? `${name} ${code}` : name;
}

/**
 * Runs `probe` (true: the site's own catalog rows are reachable) with a timeout and turns the
 * outcome into the /readyz response.
 */
export async function readinessResponse(
  probe: () => Promise<boolean>,
  timeoutMs: number = READINESS_TIMEOUT_MS,
): Promise<Response> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new ReadinessTimeout()), timeoutMs);
  });
  let ready = false;
  try {
    ready = await Promise.race([probe(), timeout]);
    if (!ready) console.error("readyz: not ready (no business profile visible to this site's role)");
  } catch (err) {
    console.error(`readyz: not ready (${describeFailure(err)})`);
  } finally {
    clearTimeout(timer);
  }
  return ready
    ? new Response("ready\n", { status: 200, headers: HEADERS })
    : new Response("not ready\n", { status: 503, headers: HEADERS });
}
