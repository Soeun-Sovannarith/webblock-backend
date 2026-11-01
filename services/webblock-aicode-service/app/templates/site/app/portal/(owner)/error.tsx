"use client";
// Owned by the WebBlock harness and refreshed on every run.
import { ErrorState } from "@/portal/components/common/error-state";

export default function Error({ error, reset }: { error: unknown; reset: () => void }) {
  return <ErrorState error={error} retry={reset} />;
}
