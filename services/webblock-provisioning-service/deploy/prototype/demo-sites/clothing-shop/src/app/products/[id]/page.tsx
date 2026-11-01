import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ProductPicture } from "@/components/ProductPicture";
import { FALLBACK_SHOP_NAME, SiteFrame } from "@/components/SiteChrome";
import { ShopUnavailable } from "@/components/ShopUnavailable";
import { isProductId, loadProduct, loadProfile, unlessUnavailable } from "@/lib/catalog";
import { displayAttributes, formatPrice } from "@/lib/format";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  // A catalog outage must not fail the metadata: the page itself then shows the error page.
  const [profile, product] = await Promise.all([
    loadProfile().catch(() => null),
    loadProduct(id).catch(() => null),
  ]);
  const shop = profile?.name ?? FALLBACK_SHOP_NAME;
  if (product === null) return { title: `Not found — ${shop}` };
  return { title: `${product.name} — ${shop}`, description: product.description ?? undefined };
}

export default async function ProductPage({ params }: Props) {
  const { id } = await params;
  if (!isProductId(id)) notFound();
  const data = await unlessUnavailable(() => Promise.all([loadProfile(), loadProduct(id)]));
  if (data === null) {
    return (
      <ShopUnavailable
        retry={
          <Link className="button" href={`/products/${id}`}>
            Try again
          </Link>
        }
      />
    );
  }
  const [profile, product] = data;
  if (product === null) notFound();

  const price = formatPrice(product.price);
  const attributes = displayAttributes(product.attributes);
  const [first, ...more] = product.images;

  return (
    <SiteFrame profile={profile}>
      <div className="container detail">
        <p className="back">
          <Link href={product.categorySlug !== null ? `/?category=${product.categorySlug}#products` : "/#products"}>
            <span aria-hidden="true">←</span> Back to {product.categoryName ?? "all products"}
          </Link>
        </p>

        <div className="detail__grid">
          <div className="gallery">
            <ProductPicture
              image={first ?? null}
              productName={product.name}
              priority
              sizes="(min-width: 900px) 50vw, 100vw"
            />
            {more.length > 0 && (
              <ul className="gallery__more" role="list">
                {more.map((img, i) => (
                  <li key={i}>
                    <ProductPicture image={img} productName={product.name} sizes="(min-width: 900px) 25vw, 50vw" />
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="detail__info">
            {product.categoryName !== null && <p className="card__category">{product.categoryName}</p>}
            <h1 className="detail__title">{product.name}</h1>
            <p className="detail__price">{price ?? "Price on request"}</p>
            {product.description !== null && <p className="detail__description">{product.description}</p>}

            {attributes.length > 0 && (
              <dl className="attributes">
                {attributes.map((a) => (
                  <div key={a.key} className="attributes__row">
                    <dt>{a.label}</dt>
                    <dd>
                      {a.isList ? (
                        <ul className="options" role="list">
                          {a.values.map((v) => (
                            <li key={v} className="option">
                              {v}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        a.values[0]
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            {profile?.email != null && (
              <p className="detail__ask">
                <a className="button button--outline" href={`mailto:${profile.email}?subject=${encodeURIComponent(product.name)}`}>
                  Ask about this item
                </a>
              </p>
            )}
          </div>
        </div>
      </div>
    </SiteFrame>
  );
}
