"use client";

import { ErrorState } from "@/components/common/error-state";

export default function Error({
  error,
  retry,
}: {
  error: unknown;
  retry: () => void;
}) {
  return <ErrorState error={error} retry={retry} />;
}
