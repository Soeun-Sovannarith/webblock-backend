// Owned by the WebBlock harness and refreshed on every run. The portal's entry: sends a signed-in owner to their
// products and anyone else to single sign-on, and explains why the portal can't open when it can't.
import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { currentOwner, portalEnabled } from "@/lib/portal-auth";
import { BrandLogo } from "@/portal/components/layout/brand-logo";

type SearchParams = Promise<{ error?: string }>;

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-page py-12">
      <section className="flex w-full max-w-[460px] flex-col gap-4 rounded-[20px] border border-border bg-background p-8 shadow-surface">
        <BrandLogo href="/" />
        <h1 className="pt-2 text-2xl font-semibold text-foreground">{title}</h1>
        {children}
      </section>
    </main>
  );
}

const linkClass = "inline-flex h-10 w-fit items-center rounded-full bg-brand px-5 text-sm font-semibold text-primary-foreground hover:bg-brand-strong";

export default async function PortalEntry({ searchParams }: { searchParams: SearchParams }) {
  const { error } = await searchParams;

  if (!portalEnabled()) {
    return (
      <Notice title="The owner portal isn’t set up yet">
        <p className="text-sm text-muted-foreground">
          It needs the database settings (PGHOST and the other PG variables, or DATABASE_URL) and the single sign-on
          settings (KEYCLOAK_ISSUER, KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET), which WebBlock sets when it deploys the
          website.
        </p>
      </Notice>
    );
  }

  if (await currentOwner()) redirect("/portal/products");

  if (error === "forbidden") {
    return (
      <Notice title="This account can’t manage the website">
        <p className="text-sm text-muted-foreground">Ask the website’s owner to add you to the project in WebBlock.</p>
        <a className={linkClass} href="/portal/login?prompt=login">Sign in with another account</a>
      </Notice>
    );
  }
  if (error === "signin") {
    return (
      <Notice title="Signing in didn’t work">
        <p className="text-sm text-muted-foreground">Please try again.</p>
        <a className={linkClass} href="/portal/login">Try again</a>
      </Notice>
    );
  }
  redirect("/portal/login"); // single sign-on: owners already signed in to WebBlock come straight back
}
