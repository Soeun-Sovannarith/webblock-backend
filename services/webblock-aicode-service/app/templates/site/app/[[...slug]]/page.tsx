import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import { connection } from "next/server";
import data from "../../puck-data.json";
import { renderPage } from "@/components/registry";
import { siteAttributes } from "@/components/blocks/theme";
import "@/components/blocks/theme.css";
import { getBusinessProfile, locationText, type BusinessProfile } from "@/lib/business";
import { listProducts, productSlug } from "@/lib/products";
import type { PuckData, SiteData, SitePage } from "@/lib/puck";

type Product = { name?: string; price?: string; description?: string; imageUrl?: string | null };
type ProductEntry = { product: Product & { name: string }; page: SitePage; listTitle?: string };

const site = data as unknown as SiteData;
const home = site.pages.find((page) => page.path === "/") ?? site.pages[0];

type Params = Promise<{ slug?: string[] }>;

function isProductCards(block: { type: string }) {
  return block.type === "ProductCards";
}

// The products the site shows. With a database (lib/db.ts) they are the available rows of the "products"
// table, which the owner manages in the product portal (/portal); without one, the products in the Puck
// ProductCards blocks. Rendered at request time, so portal changes appear immediately; cache() shares one query
// between generateMetadata and the page.
const loadProducts = cache(async (): Promise<Product[] | null> => {
  await connection();
  const rows = await listProducts();
  return rows && rows.map(({ name, price, description, imageUrl }) => ({ name, price, description: description ?? "", imageUrl }));
});

// The business profile, once the owner has saved it in the portal (/portal/info). Until then the pages show what
// onboarding or the editor wrote into them.
const loadBusiness = cache(async (): Promise<BusinessProfile | null> => {
  await connection();
  const profile = await getBusinessProfile();
  return profile?.editedInPortal ? profile : null;
});

// Request-time content from the database: the catalog for ProductCards, and the business profile for the Location
// (address, contact details, opening hours, map link) and About (description) blocks.
function withSiteData(page: SitePage, products: Product[] | null, business: BusinessProfile | null): PuckData {
  if (!products && !business) return page.data;
  return {
    ...page.data,
    content: page.data.content.map((block) => {
      if (products && isProductCards(block)) return { ...block, props: { ...block.props, products } };
      if (business && block.type === "Location") {
        return { ...block, props: { ...block.props, address: locationText(business), mapsUrl: business.mapsUrl ?? undefined } };
      }
      if (business?.description && block.type === "About") return { ...block, props: { ...block.props, text: business.description } };
      return block;
    }),
  };
}

// The product behind /products/<slug>: the first product with that slug, linked back to the first page that
// lists products (the home page if none does). A page with the same path takes precedence (see resolve).
function findProduct(slug: string, products: Product[] | null): ProductEntry | undefined {
  const matches = (p: Product | undefined) => Boolean(p?.name?.trim()) && productSlug(p!.name!.trim()) === slug;
  const listings = site.pages.flatMap((page) => page.data.content.filter(isProductCards).map((block) => ({ page, block })));
  const titleOf = (props: Record<string, unknown>) => (typeof props.title === "string" ? props.title : undefined);
  if (products) {
    const product = products.find(matches);
    const listing = listings[0];
    const page = listing?.page ?? home;
    return product && page ? { product: { ...product, name: product.name! }, page, listTitle: listing && titleOf(listing.block.props) } : undefined;
  }
  for (const { page, block } of listings) {
    const product = (Array.isArray(block.props.products) ? (block.props.products as Product[]) : []).find(matches);
    if (product) return { product: { ...product, name: product.name! }, page, listTitle: titleOf(block.props) };
  }
  return undefined;
}

async function resolve(slug: string[] | undefined) {
  const path = "/" + (slug ?? []).join("/");
  const page = site.pages.find((p) => p.path === path);
  const products = await loadProducts();
  if (page) return { page, products, business: await loadBusiness() };
  if (slug?.length === 2 && slug[0] === "products") {
    const entry = findProduct(slug[1], products);
    if (entry) return { entry };
  }
  return {};
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { page, entry } = await resolve((await params).slug);
  if (page) {
    return {
      title: page.seo_title || page.data.root.props?.title || page.name,
      description: page.seo_description || undefined,
    };
  }
  if (entry) {
    const siteTitle = entry.page.data.root.props?.title;
    return {
      title: siteTitle ? `${entry.product.name} | ${siteTitle}` : entry.product.name,
      description: entry.product.description || undefined,
    };
  }
  return {};
}

function ProductDetail({ entry }: { entry: ProductEntry }) {
  const { product, page, listTitle } = entry;
  return (
    <div {...siteAttributes(page.data.root.props)}>
      <section className="wb-section wb-product-detail">
        <div className="wb-container">
          <a className="wb-back" href={page.path}>
            ← {listTitle || page.name}
          </a>
          {product.imageUrl ? <img className="wb-product-image" src={product.imageUrl} alt={product.name} /> : null}
          <h1>{product.name}</h1>
          {product.price ? <div className="wb-price">{product.price}</div> : null}
          {product.description ? <p>{product.description}</p> : null}
        </div>
      </section>
    </div>
  );
}

export default async function Page({ params }: { params: Params }) {
  const { page, products, business, entry } = await resolve((await params).slug);
  if (page) return <main>{renderPage(withSiteData(page, products ?? null, business ?? null))}</main>;
  if (entry) return <main><ProductDetail entry={entry} /></main>;
  notFound();
}
