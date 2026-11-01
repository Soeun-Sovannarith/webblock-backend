import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

// The layout reads nothing from the catalog, so the error page can still render inside it when the
// catalog is unreachable.
export const metadata: Metadata = {
  title: { default: "Shop", template: "%s" },
  description: "Everyday clothing, made to last.",
};

export const viewport: Viewport = {
  themeColor: "#f6f2ec",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
