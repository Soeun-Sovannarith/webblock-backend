"use client";

// Last-resort error boundary for unexpected rendering errors. A catalog outage does not reach it: the
// pages render <ShopUnavailable> themselves, on the server. In production Next.js passes only an opaque
// digest to the browser, never the error's text.
import { ShopUnavailable } from "@/components/ShopUnavailable";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <ShopUnavailable
      retry={
        <button type="button" className="button" onClick={() => reset()}>
          Try again
        </button>
      }
    />
  );
}
