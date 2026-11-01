import type { NextConfig } from "next";

// The shop runs as a generated WebBlock workload: a standalone Node server on port 8080, as UID 65532,
// with a read-only root file system where only /tmp is writable. Every page reads the catalog at
// request time and the product images are static SVG files served as they are, so Next.js has nothing
// to cache or optimise on disk.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  images: { unoptimized: true },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
