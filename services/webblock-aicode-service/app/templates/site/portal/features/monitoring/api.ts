// What the site can check about itself, read at request time.
import { catalogIsReachable, listPortalProducts } from "@/lib/products";
import type { SiteHealth } from "./types";

const DATABASE_TIMEOUT_MS = 2000;

async function databaseAnswers(): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("timeout")), DATABASE_TIMEOUT_MS);
  });
  try {
    await Promise.race([catalogIsReachable(), timeout]);
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function getSiteHealth(): Promise<SiteHealth> {
  const databaseReachable = await databaseAnswers();
  let products: SiteHealth["products"] = null;
  if (databaseReachable) {
    try {
      const rows = await listPortalProducts();
      products = { total: rows.length, shown: rows.filter((p) => p.isAvailable).length };
    } catch {
      products = null;
    }
  }
  return { online: true, databaseReachable, products, checkedAt: new Date().toISOString() };
}
