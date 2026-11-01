import type { ReactNode } from "react";
import Link from "next/link";
import { Box, Element4, Menu, SearchStatus } from "iconsax-reactjs";
import { DataTable } from "@/components/common/data-table";
import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { RowsSelect } from "@/components/common/rows-select";
import { SearchInput } from "@/components/common/search-input";
import { SegmentedTabs } from "@/components/common/segmented-tabs";
import { TablePagination } from "@/components/common/table-pagination";
import { formatPrice } from "@/lib/format";
import { tableHref, type SearchParams } from "@/lib/table-query";
import { cn } from "@/lib/utils";
import { DEFAULT_PRODUCT_PAGE_SIZE, PRODUCT_PAGE_SIZES, type ProductsView as ProductsData } from "../service";
import { ProductDialogsProvider } from "./product-dialogs";
import { CategoryChip } from "./product-parts";
import { AddProductButton, ProductCard, ProductCellButton, ProductRowActions } from "./product-triggers";

/** Owner Portal → Products: grid or list (`?view=list`), search, category tabs and the product popups. */
export function ProductsView({ data, searchParams }: { data: ProductsData; searchParams: SearchParams }) {
  const { products, counts, layout } = data;
  const noProducts = counts.total === 0;
  const filtered = Boolean(data.q || data.category);

  const heading = (
    <h2 className="flex items-center gap-3 text-lg font-semibold">
      <Box aria-hidden className="size-[22px] text-brand" /> All products
    </h2>
  );

  const toolbar = (
    <>
      <SearchInput defaultValue={data.q} placeholder="Search products" label="Search products" className="h-9" />
      <SegmentedTabs
        label="Filter by category"
        className="p-[3px]"
        tabs={[
          {
            label: `All ${counts.total}`,
            href: tableHref(searchParams, { category: null, page: null }),
            active: data.category === null,
          },
          ...counts.categories.map(({ category }) => ({
            label: category,
            href: tableHref(searchParams, { category, page: null }),
            active: data.category === category,
          })),
        ]}
      />
      <div className="ml-auto flex items-center gap-2.5">
        <LayoutToggle layout={layout} searchParams={searchParams} />
        <RowsSelect value={products.size} options={PRODUCT_PAGE_SIZES} defaultSize={DEFAULT_PRODUCT_PAGE_SIZE} />
      </div>
    </>
  );

  const empty = noProducts ? (
    <EmptyState
      icon={<Box />}
      title="No products yet"
      description="Add your first product. It shows as a Product Card on your website after your next deploy."
      action={<AddProductButton label="Add your first product" />}
    />
  ) : (
    <EmptyState
      icon={<SearchStatus />}
      title="No products found"
      description={
        filtered
          ? "Nothing matches your search or category. Try another word or show all products."
          : "There are no products on this page."
      }
      action={
        <Link
          href={tableHref(searchParams, { q: null, category: null, page: null })}
          className="text-sm font-semibold text-primary hover:underline"
        >
          Show all products
        </Link>
      }
    />
  );

  return (
    <ProductDialogsProvider websiteId={data.websiteId} domain={data.domain} categories={data.categories}>
      <PageHeader
        className="mb-0 items-center"
        title="Products"
        description={
          data.domain ? `Shown as Product Cards on ${data.domain}.` : "Shown as Product Cards on your website."
        }
        actions={<AddProductButton />}
      />

      {layout === "list" ? (
        <DataTable
          className="flex-1"
          label="Products"
          itemLabel="products"
          itemLabelOne="product"
          data={products}
          searchParams={searchParams}
          rowKey={(product) => product.id}
          heading={heading}
          toolbar={toolbar}
          empty={empty}
          columns={[
            {
              key: "product",
              header: "Product",
              className: "max-w-0 pl-0",
              cell: (product) => <ProductCellButton product={product} />,
            },
            {
              key: "category",
              header: "Category",
              className: "w-[180px]",
              cell: (product) => <CategoryChip category={product.category} />,
            },
            {
              key: "price",
              header: "Price",
              sortField: "price",
              className: "w-[140px]",
              cell: (product) => (
                <span className="text-sm font-semibold text-foreground">{formatPrice(product.price, "USD")}</span>
              ),
            },
            {
              key: "actions",
              header: <span className="sr-only">Actions</span>,
              className: "w-[112px] pr-5",
              cell: (product) => <ProductRowActions product={product} />,
            },
          ]}
        />
      ) : (
        <ProductGridFrame heading={heading} toolbar={toolbar}>
          {products.items.length === 0 ? (
            <div className="flex min-h-[300px] flex-1 items-center justify-center">{empty}</div>
          ) : (
            <ul className="grid flex-1 content-start gap-6 p-6 [grid-template-columns:repeat(auto-fill,minmax(250px,1fr))]">
              {products.items.map((product, i) => (
                <li key={product.id} className="motion-safe:animate-row-in" style={{ animationDelay: `${i * 30}ms` }}>
                  <ProductCard product={product} />
                </li>
              ))}
            </ul>
          )}
          <div className="border-t border-border">
            <TablePagination
              data={products}
              searchParams={searchParams}
              itemLabel="products"
              itemLabelOne="product"
            />
          </div>
        </ProductGridFrame>
      )}
    </ProductDialogsProvider>
  );
}

/** Same card frame as `DataTable`, holding the product grid instead of rows. */
function ProductGridFrame({
  heading,
  toolbar,
  children,
}: {
  heading: ReactNode;
  toolbar: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      aria-label="Products"
      className="flex flex-1 flex-col overflow-hidden rounded-[20px] border border-border bg-background shadow-table"
    >
      <div className="px-4 pt-5 pb-1">{heading}</div>
      <div className="flex flex-wrap items-center gap-2.5 border-b border-border px-4 py-3.5">{toolbar}</div>
      {children}
    </section>
  );
}

/** Grid / list switch. Links, so the choice lives in the URL (`?view=list`). */
function LayoutToggle({ layout, searchParams }: { layout: "grid" | "list"; searchParams: SearchParams }) {
  const options = [
    { value: "grid", label: "Grid view", icon: <Element4 />, href: tableHref(searchParams, { view: null }) },
    { value: "list", label: "List view", icon: <Menu />, href: tableHref(searchParams, { view: "list" }) },
  ] as const;

  return (
    <div role="group" aria-label="Layout" className="flex gap-0.5 rounded-full border border-border bg-page p-1">
      {options.map((option) => {
        const active = option.value === layout;
        return (
          <Link
            key={option.value}
            href={option.href}
            scroll={false}
            aria-label={option.label}
            aria-current={active ? "true" : undefined}
            className={cn(
              "flex size-9 items-center justify-center rounded-full outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/40 [&_svg]:size-[18px]",
              active ? "bg-background text-brand-strong shadow-tab" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.icon}
          </Link>
        );
      })}
    </div>
  );
}
