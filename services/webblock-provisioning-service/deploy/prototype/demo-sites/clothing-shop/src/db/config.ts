// Connection settings of the catalog, read from the environment variables that the Secret
// `catalog-runtime` provides: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD and PGSSLMODE. Every key is
// required. Problems are reported by key name only; a value never appears in an error or a log.
import type { PoolConfig } from "pg";

export const CATALOG_ENV_KEYS = [
  "PGHOST",
  "PGPORT",
  "PGDATABASE",
  "PGUSER",
  "PGPASSWORD",
  "PGSSLMODE",
] as const;

/** Raised when the catalog settings are missing or invalid. The message names keys, never values. */
export class CatalogConfigError extends Error {
  readonly problems: readonly string[];

  constructor(problems: readonly string[]) {
    super(`catalog configuration is invalid: ${problems.join("; ")}`);
    this.name = "CatalogConfigError";
    this.problems = problems;
  }
}

type SslSetting = PoolConfig["ssl"];

// libpq's sslmode values, mapped to node-postgres. "disable", "allow" and "prefer" connect without
// TLS (node-postgres cannot fall back between the two); "require" encrypts without verifying the
// server, as libpq does; "verify-ca" and "verify-full" verify the server certificate.
const SSL_MODES: Readonly<Record<string, SslSetting>> = {
  disable: false,
  allow: false,
  prefer: false,
  require: { rejectUnauthorized: false },
  "verify-ca": { rejectUnauthorized: true },
  "verify-full": { rejectUnauthorized: true },
};

/** Pool size per website: the design asks for one to two server connections per website. */
export const POOL_MAX_CONNECTIONS = 2;
const CONNECT_TIMEOUT_MS = 3_000;
const STATEMENT_TIMEOUT_MS = 5_000;
const IDLE_TIMEOUT_MS = 30_000;

/**
 * Builds the pool settings from `env`. Collects every problem before failing, and never echoes a value.
 */
export function catalogPoolConfig(env: Readonly<Record<string, string | undefined>>): PoolConfig {
  const problems: string[] = [];
  const value = (key: (typeof CATALOG_ENV_KEYS)[number]): string => {
    const v = env[key];
    if (v === undefined || v.trim() === "") {
      problems.push(`${key} is not set`);
      return "";
    }
    return v;
  };

  const host = value("PGHOST");
  const portText = value("PGPORT");
  const database = value("PGDATABASE");
  const user = value("PGUSER");
  const password = value("PGPASSWORD");
  const sslMode = value("PGSSLMODE");

  let port = 0;
  if (portText !== "") {
    port = /^[0-9]{1,5}$/.test(portText) ? Number(portText) : 0;
    if (port < 1 || port > 65_535) problems.push("PGPORT is not a port number");
  }
  let ssl: SslSetting = false;
  if (sslMode !== "") {
    if (Object.hasOwn(SSL_MODES, sslMode)) ssl = SSL_MODES[sslMode];
    else problems.push("PGSSLMODE is not a libpq sslmode");
  }
  if (problems.length > 0) throw new CatalogConfigError(problems);

  return {
    host,
    port,
    database,
    user,
    password,
    ssl,
    max: POOL_MAX_CONNECTIONS,
    connectionTimeoutMillis: CONNECT_TIMEOUT_MS,
    idleTimeoutMillis: IDLE_TIMEOUT_MS,
    statement_timeout: STATEMENT_TIMEOUT_MS,
    query_timeout: STATEMENT_TIMEOUT_MS + 1_000,
    application_name: "clothing-shop",
  };
}
