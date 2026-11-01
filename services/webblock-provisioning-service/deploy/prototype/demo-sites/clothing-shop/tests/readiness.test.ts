// /readyz contract (decision 0014 section 15): 200 only when a catalog query as the site's own role
// succeeds within a short timeout; 503 with a tiny body otherwise; nothing secret in the log.
import { afterEach, describe, expect, it, vi } from "vitest";

import { readinessResponse } from "@/lib/readiness";

afterEach(() => {
  vi.restoreAllMocks();
});

async function check(probe: () => Promise<boolean>, timeoutMs = 50) {
  const res = await readinessResponse(probe, timeoutMs);
  return { status: res.status, body: await res.text(), cache: res.headers.get("cache-control") };
}

describe("readinessResponse", () => {
  it("answers 200 when the catalog query succeeds", async () => {
    expect(await check(async () => true)).toEqual({ status: 200, body: "ready\n", cache: "no-store" });
  });

  it("answers 503 when the query finds no profile for this site's role", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(await check(async () => false)).toEqual({ status: 503, body: "not ready\n", cache: "no-store" });
  });

  it("answers 503 when the query fails, and logs no error text", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const failure = Object.assign(new Error('password authentication failed, password="s3cret"'), {
      code: "28P01",
    });
    expect(await check(async () => Promise.reject(failure))).toEqual({
      status: 503,
      body: "not ready\n",
      cache: "no-store",
    });
    const logged = log.mock.calls.flat().map(String).join(" ");
    expect(logged).toContain("28P01");
    expect(logged).not.toContain("s3cret");
    expect(logged).not.toContain("password authentication");
  });

  it("answers 503 when the query does not finish within the timeout", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const started = Date.now();
    const result = await check(() => new Promise<boolean>(() => {}), 30);
    expect(result).toEqual({ status: 503, body: "not ready\n", cache: "no-store" });
    expect(Date.now() - started).toBeLessThan(1_000);
  });
});
