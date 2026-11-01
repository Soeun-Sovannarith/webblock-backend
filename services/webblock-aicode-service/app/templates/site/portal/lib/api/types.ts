/** Paginated list response. `page` is zero-based. */
export type Page<T> = {
  items: T[];
  page: number;
  size: number;
  totalItems: number;
};
