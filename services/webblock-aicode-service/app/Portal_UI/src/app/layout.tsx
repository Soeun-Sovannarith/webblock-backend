import type { Metadata } from "next";
import type { ReactNode } from "react";
import { DM_Sans, Familjen_Grotesk } from "next/font/google";
import { Toaster } from "@/components/common/toast";
import { siteConfig } from "@/config/site";
import { cn } from "@/lib/utils";
import "./globals.css";

const familjenGrotesk = Familjen_Grotesk({
  variable: "--font-familjen-grotesk",
  subsets: ["latin"],
});

const dmSans = DM_Sans({
  variable: "--font-dm-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: siteConfig.name, template: `%s · ${siteConfig.name}` },
  description: siteConfig.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={cn(familjenGrotesk.variable, dmSans.variable, "antialiased")}
    >
      <body className="min-h-dvh">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
