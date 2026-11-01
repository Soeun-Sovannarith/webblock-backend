import Link from "next/link";
import type { ReactNode } from "react";

import type { ShopProfile } from "@/lib/catalog";
import { formatOpeningRange } from "@/lib/format";

export const FALLBACK_SHOP_NAME = "Our shop";

/** The header shared by every page: the shop's name as the home link. */
export function SiteHeader({ shopName }: { shopName: string }) {
  return (
    <header className="site-header">
      <div className="container site-header__inner">
        <Link href="/" className="brand">
          {shopName}
        </Link>
        <nav aria-label="Main">
          <Link href="/#products" className="nav-link">
            Shop all
          </Link>
        </nav>
      </div>
    </header>
  );
}

/** The footer: contact details and opening hours from the business profile, when present. */
export function SiteFooter({ profile }: { profile: ShopProfile | null }) {
  const name = profile?.name ?? FALLBACK_SHOP_NAME;
  const hours = profile?.openingHours ?? [];
  return (
    <footer className="site-footer">
      <div className="container site-footer__grid">
        <div>
          <p className="site-footer__name">{name}</p>
          {profile?.tagline != null && <p className="muted">{profile.tagline}</p>}
        </div>
        {(profile?.address != null || profile?.phone != null || profile?.email != null) && (
          <div>
            <h2 className="site-footer__heading">Visit or contact us</h2>
            <address>
              {profile?.address != null && <p>{profile.address}</p>}
              {profile?.phone != null && (
                <p>
                  <a href={`tel:${profile.phone.replace(/[^0-9+]/g, "")}`}>{profile.phone}</a>
                </p>
              )}
              {profile?.email != null && (
                <p>
                  <a href={`mailto:${profile.email}`}>{profile.email}</a>
                </p>
              )}
            </address>
          </div>
        )}
        {hours.length > 0 && (
          <div>
            <h2 className="site-footer__heading">Opening hours</h2>
            <ul className="plain-list">
              {hours.map((range, i) => (
                <li key={i}>{formatOpeningRange(range)}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="container site-footer__legal">
        <p className="muted">
          © {name}. Prices in US dollars. Product illustrations are original artwork.
        </p>
      </div>
    </footer>
  );
}

/** Header, main landmark and footer around a page's content. */
export function SiteFrame({ profile, children }: { profile: ShopProfile | null; children: ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteHeader shopName={profile?.name ?? FALLBACK_SHOP_NAME} />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <SiteFooter profile={profile} />
    </>
  );
}
