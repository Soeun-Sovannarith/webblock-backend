// The shop's one database handle: a small node-postgres pool, wrapped by Drizzle, created on first use
// so that building the image and answering /healthz never need the catalog.
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import { catalogPoolConfig } from "./config";
import * as schema from "./schema";

export type CatalogDb = NodePgDatabase<typeof schema>;

let db: CatalogDb | undefined;

/** Returns the shared Drizzle handle, creating the pool from the environment on the first call. */
export function catalogDb(): CatalogDb {
  if (db === undefined) {
    const pool = new Pool(catalogPoolConfig(process.env));
    // An idle connection that breaks (database restart, network loss) emits "error" on the pool; left
    // unhandled it would end the process. The pool drops that connection and opens a new one later.
    pool.on("error", (err: Error & { code?: string }) => {
      console.error(`catalog: idle connection lost (${err.code ?? "unknown"})`);
    });
    db = drizzle(pool, { schema });
  }
  return db;
}
