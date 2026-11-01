import type { Metadata } from "next";
import Link from "next/link";

import { ProductCard } from "@/components/ProductCard";
import { FALLBACK_SHOP_NAME, SiteFrame } from "@/components/SiteChrome";
import { ShopUnavailable } from "@/components/ShopUnavailable";
import { isCategorySlug, loadCategories, loadProducts, loadProfile, unlessUnavailable } from "@/lib/catalog";

// Every request reads the catalog; nothing is prerendered at build time or cached on disk.
export const dynamic = "force-dynamic";

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export async function generateMetadata(): Promise<Metadata> {
  // A catalog outage must not fail the metadata: the page itself then shows the error page.
  const profile = await loadProfile().catch(() => null);
  const name = profile?.name ?? FALLBACK_SHOP_NAME;
  return {
    title: name,
    description: profile?.tagline ?? undefined,
  };
}

export default async function HomePage({ searchParams }: Props) {
  const params = await searchParams;
  const requested = typeof params.category === "string" ? params.category : undefined;
  const categorySlug = requested !== undefined && isCategorySlug(requested) ? requested : undefined;

  const data = await unlessUnavailable(() =>
    Promise.all([loadProfile(), loadCategories(), loadProducts(categorySlug)]),
  );
  if (data === null) return <ShopUnavailable retry={<Link className="button" href="/">Try again</Link>} />;
  const [profile, categoryList, productList] = data;
  const name = profile?.name ?? FALLBACK_SHOP_NAME;
  const activeCategory = categoryList.find((c) => c.slug === categorySlug);

  return (
    <SiteFrame profile={profile}>
      <section className="hero" aria-labelledby="shop-name">
        <div className="container">
          <p className="hero__eyebrow">New season collection</p>
          <h1 id="shop-name" className="hero__title">
            {name}
          </h1>
          {profile?.tagline != null && <p className="hero__tagline">{profile.tagline}</p>}
        </div>
      </section>

      <section id="products" className="container products" aria-labelledby="products-heading">
        <div className="products__head">
          <h2 id="products-heading" className="section-title">
            {activeCategory?.name ?? "All products"}
          </h2>
          <p className="muted" aria-live="polite">
            {productList.length === 1 ? "1 product" : `${productList.length} products`}
          </p>
        </div>

        {categoryList.length > 0 && (
          <nav aria-label="Categories" className="filters">
            <Link href="/#products" className="chip" aria-current={categorySlug === undefined ? "page" : undefined}>
              All
            </Link>
            {categoryList.map((c) => (
              <Link
                key={c.slug}
                href={`/?category=${encodeURIComponent(c.slug)}#products`}
                className="chip"
                aria-current={c.slug === categorySlug ? "page" : undefined}
              >
                {c.name}
              </Link>
            ))}
          </nav>
        )}

        {productList.length === 0 ? (
          <p className="empty">No products here yet. Please check back soon.</p>
        ) : (
          <ul className="grid" role="list">
            {productList.map((p) => (
              <li key={p.id}>
                <ProductCard product={p} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </SiteFrame>
  );
}
