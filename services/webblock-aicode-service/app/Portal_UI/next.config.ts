import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    // Product photo and logo uploads go through Server Actions (up to 5 MB + form overhead).
    serverActions: { bodySizeLimit: "6mb" },
  },
  turbopack: {
    // This folder is its own app, even when it sits inside another project.
    root: __dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
