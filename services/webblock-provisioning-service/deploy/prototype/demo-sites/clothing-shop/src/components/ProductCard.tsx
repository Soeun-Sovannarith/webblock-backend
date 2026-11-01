import Link from "next/link";

import { ProductPicture } from "@/components/ProductPicture";
import type { ProductSummary } from "@/lib/catalog";
import { formatPrice } from "@/lib/format";

/** One product in the shop grid: picture, category, name, price and a "View details" link. */
export function ProductCard({ product }: { product: ProductSummary }) {
  const price = formatPrice(product.price);
  const href = `/products/${product.id}`;
  const titleId = `product-${product.id}`;
  return (
    <article className="card" aria-labelledby={titleId}>
      <ProductPicture
        image={product.image}
        productName={product.name}
        sizes="(min-width: 1100px) 25vw, (min-width: 720px) 33vw, (min-width: 480px) 50vw, 100vw"
      />
      <div className="card__body">
        {product.categoryName !== null && <p className="card__category">{product.categoryName}</p>}
        <h3 className="card__name" id={titleId}>
          {product.name}
        </h3>
        <p className="card__price">{price ?? "Price on request"}</p>
        <Link className="button" href={href} aria-label={`View details of ${product.name}`}>
          View details
        </Link>
      </div>
    </article>
  );
}
