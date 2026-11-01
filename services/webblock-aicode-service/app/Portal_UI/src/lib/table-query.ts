// Table state (page, size, sort) lives in the URL. These helpers turn the page's `searchParams`
// into the query a feature's api.ts expects, and build links that change one part of it.
import type { Page } from "@/lib/api/types";

/** The resolved `searchParams` prop of a page. */
export type SearchParams = Record<string, string | string[] | undefined>;

export type SortDirection = "asc" | "desc";

export type TableQuery = {
  /** Zero-based, like `Page<T>.page` and Spring's Pageable. */
  page: number;
  size: number;
  sort?: { field: string; direction: SortDirection };
};

const DEFAULT_SIZE = 10;
const MAX_SIZE = 100;

/** Largest page size the URL accepts (bigger values fall back to the default). */
export const MAX_PAGE_SIZE = MAX_SIZE;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * URL → api.ts query. In the URL `page` is 1-based (what people see), `size` is rows per page and
 * `sort` uses Spring's format `field,asc|desc`. Invalid values fall back to the defaults.
 *
 * @example
 * const query = parseTableQuery(await searchParams, { sort: { field: "joinedAt", direction: "desc" } });
 * const users = await listUsers(query);
 */
export function parseTableQuery(
  searchParams: SearchParams,
  defaults: { size?: number; sort?: TableQuery["sort"] } = {},
): TableQuery {
  const pageNumber = Number.parseInt(first(searchParams.page) ?? "", 10);
  const size = Number.parseInt(first(searchParams.size) ?? "", 10);
  const [field, direction] = (first(searchParams.sort) ?? "").split(",");

  return {
    page: Number.isInteger(pageNumber) && pageNumber > 1 ? pageNumber - 1 : 0,
    size:
      Number.isInteger(size) && size > 0 && size <= MAX_SIZE
        ? size
        : (defaults.size ?? DEFAULT_SIZE),
    sort:
      field && (direction === "asc" || direction === "desc")
        ? { field, direction }
        : defaults.sort,
  };
}

/** Same URL with some params changed; `null` removes a param. Returns a relative `?…` href. */
export function tableHref(
  searchParams: SearchParams,
  changes: Record<string, string | number | null>,
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    for (const v of Array.isArray(value) ? value : [value]) {
      if (v !== undefined) params.append(key, v);
    }
  }
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) params.delete(key);
    else params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "?";
}

/** Zero-based page indexes to show, with "ellipsis" for gaps: [0, "ellipsis", 4, 5, 6, "ellipsis", 11]. */
export function pageRange(
  current: number,
  totalPages: number,
): (number | "ellipsis")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i);

  const pages = new Set([0, totalPages - 1, current - 1, current, current + 1]);
  if (current <= 2) [1, 2, 3].forEach((p) => pages.add(p));
  if (current >= totalPages - 3) [totalPages - 4, totalPages - 3, totalPages - 2].forEach((p) => pages.add(p));

  const sorted = [...pages].filter((p) => p >= 0 && p < totalPages).sort((a, b) => a - b);
  const result: (number | "ellipsis")[] = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) result.push("ellipsis");
    result.push(p);
  });
  return result;
}

/** For mock api.ts only: slice an in-memory array into a `Page<T>`. Spring does this for real. */
export function toPage<T>(items: T[], { page, size }: Pick<TableQuery, "page" | "size">): Page<T> {
  return {
    items: items.slice(page * size, page * size + size),
    page,
    size,
    totalItems: items.length,
  };
}
