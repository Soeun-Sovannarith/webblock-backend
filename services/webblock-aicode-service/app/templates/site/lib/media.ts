// Owned by the WebBlock harness and refreshed on every run. How the /media routes answer with a stored image.
import type { StoredFile } from "./storage";

// Only these types are served as images; anything else is sent as a download.
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/svg+xml"]);

export function fileResponse(file: StoredFile | null): Response {
  if (!file) return new Response(null, { status: 404 });
  const image = IMAGE_TYPES.has(file.contentType);
  return new Response(new Uint8Array(file.bytes), {
    headers: {
      "Content-Type": image ? file.contentType : "application/octet-stream",
      // Page URLs carry ?v=, which changes with the file, so a replaced image gets a new URL.
      "Cache-Control": "public, max-age=86400",
      "X-Content-Type-Options": "nosniff",
      // An SVG opened on its own runs no script and loads nothing.
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
      ...(image ? {} : { "Content-Disposition": "attachment" }),
    },
  });
}
