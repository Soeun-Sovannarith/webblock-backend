// Owned by the WebBlock harness and refreshed on every run. The site's Postgres connection, or null when the site
// runs without a database: it then shows the products from puck-data.json. The site's data lives in WebBlock's shared
// "catalog" schema (db/schema.ts). The site logs in as its own database role, and row-level security shows that role
// only its own website's rows and fills in website_id on insert, so queries never name it. The connection comes from
// the libpq variables PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD and PGSSLMODE (Provisioning's site contract), which
// the WebBlock harness sets at deploy time; DATABASE_URL also works, for local development. Read at request time,
// never during the build. Problems are logged by variable name, never by value.
import postgres from "postgres";

type Ssl = postgres.Options<Record<string, postgres.PostgresType>>["ssl"];

// libpq sslmode values: disable, allow and prefer connect without TLS, require encrypts without verifying the
// server, verify-ca and verify-full verify it (verify-ca without the host name check).
const SSL_MODES: Record<string, Ssl> = {
  disable: false,
  allow: false,
  prefer: false,
  require: "require",
  "verify-ca": { rejectUnauthorized: true, checkServerIdentity: () => undefined },
  "verify-full": "verify-full",
};

// The platform allows one to two connections per site; tables are named without their schema.
const POOL = { max: 2, idle_timeout: 20, connect_timeout: 3, connection: { search_path: "catalog" } };

function connect(env: NodeJS.ProcessEnv): postgres.Sql | null {
  if (env.PGHOST) {
    const missing = ["PGDATABASE", "PGUSER", "PGPASSWORD"].filter((key) => !env[key]?.trim());
    const port = Number(env.PGPORT || 5432);
    if (!Number.isInteger(port) || port < 1 || port > 65535) missing.push("PGPORT (not a port number)");
    const sslMode = env.PGSSLMODE || "prefer";
    if (!Object.hasOwn(SSL_MODES, sslMode)) missing.push("PGSSLMODE (not a libpq sslmode)");
    if (missing.length > 0) {
      console.error(`[webblock] database settings are incomplete: ${missing.join(", ")}`);
      return null;
    }
    return postgres({
      ...POOL,
      host: env.PGHOST,
      port,
      database: env.PGDATABASE,
      username: env.PGUSER,
      password: env.PGPASSWORD,
      ssl: SSL_MODES[sslMode],
    });
  }
  return env.DATABASE_URL ? postgres(env.DATABASE_URL, POOL) : null;
}

const cache = globalThis as unknown as { webblockSql?: postgres.Sql | null; webblockWebsiteId?: string };

export function getSql(): postgres.Sql | null {
  if (cache.webblockSql === undefined) cache.webblockSql = connect(process.env);
  return cache.webblockSql;
}

/** This site's website id (its WebBlock project id) as the database maps its role, or null (no database, or the
 * website is not active). Used for object storage keys; queries never need it. */
export async function currentWebsiteId(): Promise<string | null> {
  if (cache.webblockWebsiteId) return cache.webblockWebsiteId;
  const sql = getSql();
  if (!sql) return null;
  const [row] = await sql<{ id: string | null }[]>`SELECT current_website_id() AS id`;
  if (row?.id) cache.webblockWebsiteId = row.id;
  return row?.id ?? null;
}
