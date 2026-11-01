/** Return type of every Server Action. Expected failures are returned, not thrown. */
export type ActionResult<T = undefined> =
  | { ok: true; data: T; message?: string }
  | {
      ok: false;
      message: string;
      fieldErrors?: Partial<Record<string, string[]>>;
      values?: Record<string, string>;
    };
