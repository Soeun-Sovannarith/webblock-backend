"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";

/**
 * Change one URL search param (filters, search, rows per page). Empty or `null` removes it and the
 * page goes back to 1. The server re-renders with the new params, so state lives in the URL.
 */
export function useUrlParam() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();

  function setParam(name: string, value: string | null) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(name, value);
    else params.delete(name);
    params.delete("page");
    const query = params.toString();
    startTransition(() => {
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    });
  }

  return { setParam, pending, searchParams };
}
