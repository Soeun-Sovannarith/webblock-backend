import type { Metadata } from "next";
import type { ReactNode } from "react";
import data from "../puck-data.json";
import type { SiteData } from "@/lib/puck";
import "./globals.css";

const site = data as unknown as SiteData;
const home = site.pages.find((page) => page.path === "/") ?? site.pages[0];

export const metadata: Metadata = {
  title: home?.data.root.props?.title || "WebBlock site",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
