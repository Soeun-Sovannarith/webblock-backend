// Owned by the WebBlock harness and refreshed on every run. The site runs as a standalone Node server (the
// Dockerfile copies .next/standalone), with a read-only root file system where only /tmp is writable.
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  images: { unoptimized: true },
  experimental: {
    // Owner portal photo and logo uploads go through Server Actions (up to 5 MB plus the form).
    serverActions: { bodySizeLimit: "6mb" },
  },
  turbopack: {
    rules: {
      // Only the owner portal's stylesheet (portal/portal.tailwind.css) goes through Tailwind; the storefront's CSS
      // is plain and stays as it is.
      "*.tailwind.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
