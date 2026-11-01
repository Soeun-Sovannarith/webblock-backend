import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The friendly page shown when the shop cannot read its catalog. It is rendered on the server, so it
 * works without JavaScript, reveals nothing about the cause, and asks search engines not to index it.
 */
export function ShopUnavailable({ retry }: { retry: ReactNode }) {
  return (
    <>
      <meta name="robots" content="noindex" />
      <header className="site-header">
        <div className="container site-header__inner">
          <Link href="/" className="brand">
            Shop
          </Link>
        </div>
      </header>
      <main id="main" className="container notice" role="alert">
        <p className="hero__eyebrow">Temporarily unavailable</p>
        <h1 className="notice__title">We&rsquo;ll be right back</h1>
        <p className="notice__text">
          Our shop can&rsquo;t show its products at the moment. Nothing is wrong on your side. Please try
          again in a minute.
        </p>
        <p>{retry}</p>
      </main>
    </>
  );
}
