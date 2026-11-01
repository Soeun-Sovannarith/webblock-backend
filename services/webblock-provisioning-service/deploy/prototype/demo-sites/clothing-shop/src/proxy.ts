// Answers the catalog pages with 503 while the catalog is unreachable (see lib/outage.ts). It runs on
// the Node runtime, so it reaches the catalog with the same pool as the pages.
import { NextResponse, type NextRequest } from "next/server";

import { CatalogConfigError } from "@/db/config";
import { catalogIsReady } from "@/lib/catalog";
import { gatesPath, outageGate } from "@/lib/outage";

async function probe(): Promise<boolean> {
  try {
    return await catalogIsReady();
  } catch (err) {
    // Names the missing or invalid settings, never a value (as the page reads do).
    if (err instanceof CatalogConfigError) console.error(`catalog: gate failed: ${err.message}`);
    throw err;
  }
}

export async function proxy(request: NextRequest): Promise<Response> {
  if (!gatesPath(request.nextUrl.pathname)) return NextResponse.next();
  return (await outageGate(probe)) ?? NextResponse.next();
}

export const config = {
  matcher: ["/", "/products/:id"],
};
