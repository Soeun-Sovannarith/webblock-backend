export type Product = { name?: string; price?: string; description?: string };
export type ProductCardsProps = { title?: string; products?: Product[] };

// Each named product links to its detail page, /products/<slug>, served by app/[[...slug]]/page.tsx.
// Keep in sync with productSlug in lib/products.ts (blocks import only React, so it is repeated here).
function productSlug(name: string) {
  const slug = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (slug) return slug;
  let hash = 5381;
  for (const ch of name) hash = (hash * 33 + ch.codePointAt(0)!) >>> 0;
  return "item-" + hash.toString(36);
}

export default function ProductCards({ title = "Products", products = [] }: ProductCardsProps) {
  return (
    <section className="wb-section">
      <div className="wb-container">
        <h2>{title}</h2>
        <div className="wb-products">
          {products.map((product, i) => (
            <article className="wb-product" key={i}>
              <h3>
                {product.name?.trim() ? (
                  <a className="wb-product-link" href={`/products/${productSlug(product.name.trim())}`}>
                    {product.name}
                  </a>
                ) : (
                  product.name
                )}
              </h3>
              {product.price ? <div className="wb-price">{product.price}</div> : null}
              {product.description ? <p className="wb-product-summary">{product.description}</p> : null}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
