"use client";

/** Fallback UI for error.tsx boundaries: message + retry. */
export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry: () => void;
}) {
  const message =
    error instanceof Error && error.message
      ? error.message
      : "Something went wrong. Please try again.";

  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border px-6 py-16 text-center"
    >
      <h2 className="text-lg font-semibold">Something went wrong</h2>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={() => retry()}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        Try again
      </button>
    </div>
  );
}
