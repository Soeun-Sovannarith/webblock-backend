// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProductCard } from "@/components/ProductCard";
import type { ProductSummary } from "@/lib/catalog";

const tee: ProductSummary = {
  id: "0b6f7a52-3c1d-4e8f-9a2b-5c6d7e8f9a0b",
  name: "Organic Cotton Crew Tee",
  categoryName: "Tops",
  price: "28.00",
  image: { src: "/products/crew-tee-white.svg", alt: "Off-white crew-neck T-shirt laid flat" },
};

function render(product: ProductSummary): Document {
  const html = renderToStaticMarkup(<ProductCard product={product} />);
  return new DOMParser().parseFromString(html, "text/html");
}

describe("ProductCard", () => {
  it("shows the picture, category, name, formatted price and a details link", () => {
    const doc = render(tee);

    const img = doc.querySelector("img");
    expect(img?.getAttribute("src")).toBe("/products/crew-tee-white.svg");
    expect(img?.getAttribute("alt")).toBe("Off-white crew-neck T-shirt laid flat");

    expect(doc.querySelector(".card__category")?.textContent).toBe("Tops");
    expect(doc.querySelector("h3")?.textContent).toBe("Organic Cotton Crew Tee");
    expect(doc.querySelector(".card__price")?.textContent).toBe("$28.00");

    const link = doc.querySelector("a");
    expect(link?.textContent).toBe("View details");
    expect(link?.getAttribute("href")).toBe(`/products/${tee.id}`);
    expect(link?.getAttribute("aria-label")).toBe("View details of Organic Cotton Crew Tee");

    const article = doc.querySelector("article");
    expect(article?.getAttribute("aria-labelledby")).toBe(doc.querySelector("h3")?.id);
  });

  it("shows a placeholder without a picture and a note without a price", () => {
    const doc = render({ ...tee, image: null, price: null, categoryName: null });

    expect(doc.querySelector("img")).toBeNull();
    expect(doc.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe(
      "No picture yet for Organic Cotton Crew Tee",
    );
    expect(doc.querySelector(".card__price")?.textContent).toBe("Price on request");
    expect(doc.querySelector(".card__category")).toBeNull();
  });

  it("escapes catalog text instead of rendering it as markup", () => {
    const doc = render({ ...tee, name: '<script>alert("x")</script>' });
    expect(doc.querySelector("script")).toBeNull();
    expect(doc.querySelector("h3")?.textContent).toBe('<script>alert("x")</script>');
  });
});
