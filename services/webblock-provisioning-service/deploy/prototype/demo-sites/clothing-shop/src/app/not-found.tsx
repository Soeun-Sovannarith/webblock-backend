import Link from "next/link";

// Rendered for unknown paths and for products that do not exist or are not published. It reads
// nothing from the catalog.
export default function NotFound() {
  return (
    <>
      <header className="site-header">
        <div className="container site-header__inner">
          <Link href="/" className="brand">
            Shop
          </Link>
        </div>
      </header>
      <main id="main" className="container notice">
        <p className="hero__eyebrow">404</p>
        <h1 className="notice__title">We couldn&rsquo;t find that page</h1>
        <p className="notice__text">The product may have sold out or moved. Have a look at what is in store today.</p>
        <p>
          <Link className="button" href="/#products">
            Browse all products
          </Link>
        </p>
      </main>
    </>
  );
}
