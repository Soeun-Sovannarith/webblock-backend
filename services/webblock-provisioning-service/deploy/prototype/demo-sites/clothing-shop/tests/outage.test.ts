// A catalog outage must be visible in the HTTP status: the friendly page is served with 503 and a
// Retry-After, never 200 (an outage page with 200 looks like a shop with no products to a client).
import { afterEach, describe, expect, it, vi } from "vitest";

import { OUTAGE_STATUS, outageGate, outageResponse } from "@/lib/outage";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("outageResponse", () => {
  it("is a 503 with the friendly text, not cached, not indexed, and a retry hint", async () => {
    const res = outageResponse();
    expect(OUTAGE_STATUS).toBe(503);
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).not.toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("content-type")).toContain("text/html");
    const body = await res.text();
    expect(body).toContain("be right back");
    expect(body).toContain('name="robots" content="noindex"');
  });
});

describe("outageGate", () => {
  it("lets the request through when the catalog answers", async () => {
    expect(await outageGate(async () => true, 50)).toBeNull();
  });

  it("answers 503 when the catalog has no profile for this site", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await outageGate(async () => false, 50))?.status).toBe(503);
  });

  it("answers 503 when the probe fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await outageGate(() => Promise.reject(new Error("boom")), 50))?.status).toBe(503);
  });

  it("answers 503 when the probe does not finish in time", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await outageGate(() => new Promise<boolean>(() => {}), 30))?.status).toBe(503);
  });
});

describe("gatesPath", () => {
  it.each([
    ["/", true],
    ["/products/0b6f7a52-3c1d-4e8f-9a2b-5c6d7e8f9a0b", true],
    ["/products/not-a-product", false],
    ["/readyz", false],
    ["/healthz", false],
    ["/products/crew-tee-white.svg", false],
    ["/anything-else", false],
  ])("%s -> %s", async (path, expected) => {
    const { gatesPath } = await import("@/lib/outage");
    expect(gatesPath(path)).toBe(expected);
  });
});
