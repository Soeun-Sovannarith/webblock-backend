import { describe, expect, it } from "vitest";

import { CatalogConfigError, catalogPoolConfig, POOL_MAX_CONNECTIONS } from "@/db/config";

const SECRET = "s3cr3t-Password!";
const env = {
  PGHOST: "webblock-catalog-db",
  PGPORT: "5432",
  PGDATABASE: "catalog",
  PGUSER: "site_0123456789abcdef0123456789abcdef",
  PGPASSWORD: SECRET,
  PGSSLMODE: "disable",
};

function problemsOf(e: Record<string, string | undefined>): readonly string[] {
  try {
    catalogPoolConfig(e);
  } catch (err) {
    expect(err).toBeInstanceOf(CatalogConfigError);
    const error = err as CatalogConfigError;
    expect(error.message).not.toContain(SECRET);
    return error.problems;
  }
  throw new Error("expected a CatalogConfigError");
}

describe("catalogPoolConfig", () => {
  it("builds a small pool from the catalog-runtime keys", () => {
    const c = catalogPoolConfig(env);
    expect(c).toMatchObject({
      host: "webblock-catalog-db",
      port: 5432,
      database: "catalog",
      user: env.PGUSER,
      password: SECRET,
      ssl: false,
      max: POOL_MAX_CONNECTIONS,
    });
    expect(POOL_MAX_CONNECTIONS).toBeLessThanOrEqual(2);
    expect(c.connectionTimeoutMillis).toBeGreaterThan(0);
    expect(c.statement_timeout).toBeGreaterThan(0);
  });

  it("maps libpq sslmode values", () => {
    expect(catalogPoolConfig({ ...env, PGSSLMODE: "require" }).ssl).toEqual({ rejectUnauthorized: false });
    expect(catalogPoolConfig({ ...env, PGSSLMODE: "verify-full" }).ssl).toEqual({ rejectUnauthorized: true });
    expect(catalogPoolConfig({ ...env, PGSSLMODE: "prefer" }).ssl).toBe(false);
  });

  it("reports every missing key at once, by name only", () => {
    expect(problemsOf({})).toEqual([
      "PGHOST is not set",
      "PGPORT is not set",
      "PGDATABASE is not set",
      "PGUSER is not set",
      "PGPASSWORD is not set",
      "PGSSLMODE is not set",
    ]);
    expect(problemsOf({ ...env, PGPASSWORD: " " })).toEqual(["PGPASSWORD is not set"]);
  });

  it("refuses an invalid port or sslmode without echoing the value", () => {
    expect(problemsOf({ ...env, PGPORT: "70000", PGSSLMODE: "sometimes" })).toEqual([
      "PGPORT is not a port number",
      "PGSSLMODE is not a libpq sslmode",
    ]);
    expect(problemsOf({ ...env, PGPORT: SECRET })).toEqual(["PGPORT is not a port number"]);
    expect(problemsOf({ ...env, PGSSLMODE: "__proto__" })).toEqual(["PGSSLMODE is not a libpq sslmode"]);
  });
});
