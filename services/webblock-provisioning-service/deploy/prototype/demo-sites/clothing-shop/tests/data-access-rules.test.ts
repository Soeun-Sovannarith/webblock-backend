// Source-level guard for the shop's data-access rules: Drizzle's query builder only (no raw or
// templated SQL, no direct driver queries), no filtering by website (row-level security does it), and
// no use of catalog.websites, which a site role cannot read.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

const src = join(import.meta.dirname, "..", "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

// Comments may explain the rules; only code is checked.
const withoutComments = (text: string): string =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const files = sourceFiles(src).map((p) => ({
  path: relative(src, p),
  text: withoutComments(readFileSync(p, "utf8")),
}));

const FORBIDDEN: [RegExp, string][] = [
  [/\bsql\s*\.\s*raw\b/, "sql.raw"],
  [/\bsql\s*`/, "templated SQL"],
  [/import\s*\{[^}]*\bsql\b[^}]*\}\s*from\s*["']drizzle-orm["']/, "importing sql from drizzle-orm"],
  [/\.execute\s*\(/, "db.execute"],
  [/\.query\s*\(/, "a direct driver query"],
  [/\bwebsites\b/, "the catalog.websites table"],
];

describe("data-access rules", () => {
  it("finds the source files", () => {
    expect(files.map((f) => f.path)).toContain(join("lib", "catalog.ts"));
  });

  it.each(FORBIDDEN.map(([pattern, what]) => ({ pattern, what })))("no source file uses $what", ({ pattern }) => {
    const offenders = files
      .filter((f) => pattern.test(f.text))
      .map((f) => f.path);
    expect(offenders).toEqual([]);
  });

  it("only the schema names website_id: no query filters or joins by website", () => {
    const offenders = files
      .filter((f) => f.path !== join("db", "schema.ts") && /websiteId|website_id/.test(f.text))
      .map((f) => f.path);
    expect(offenders).toEqual([]);
  });

  it("every query that reads products requires the published status", () => {
    const catalog = files.find((f) => f.path === join("lib", "catalog.ts"))?.text ?? "";
    // One chunk per query: from one catalogDb() call to the next.
    const queries = catalog.split("catalogDb()").slice(1);
    const productQueries = queries.filter((q) => /\.(from|innerJoin|leftJoin)\(products\b/.test(q));
    expect(productQueries.length).toBeGreaterThanOrEqual(3);
    for (const q of productQueries) expect(q).toContain('eq(products.status, "published")');
  });
});
