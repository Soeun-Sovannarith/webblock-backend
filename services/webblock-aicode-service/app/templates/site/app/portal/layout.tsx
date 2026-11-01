// Owned by the WebBlock harness and refreshed on every run. The owner portal (/portal): the site's owners sign in
// through WebBlock single sign-on (lib/portal-auth.ts) and manage its products and business details. Its screens live
// in portal/; its stylesheet loads only here, so the storefront keeps its own look.
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Toaster } from "@/portal/components/common/toast";
import "@/portal/portal.tailwind.css";

export const metadata: Metadata = {
  title: { default: "Owner portal", template: "%s · Owner portal" },
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-background font-sans text-foreground antialiased">
      {children}
      <Toaster />
    </div>
  );
}
