/** Generic page loading skeleton for loading.tsx: title bar + content rows. */
export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading" className="animate-pulse">
      <div className="mb-6 h-8 w-48 rounded-md bg-surface" />
      <div className="space-y-3">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-12 rounded-md bg-surface" />
        ))}
      </div>
    </div>
  );
}
