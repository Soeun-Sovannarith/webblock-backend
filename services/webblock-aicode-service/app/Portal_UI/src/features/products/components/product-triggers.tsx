"use client";

import { Add, Edit2, More, Trash } from "iconsax-reactjs";
import { Popover } from "@/components/common/popover";
import { ProductImage } from "@/components/common/product-image";
import { Button } from "@/components/ui/button";
import { formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Product } from "../types";
import { useProductDialogs } from "./product-dialogs";

// Small client leaves that open the product popups. The lists around them stay Server Components.

export function AddProductButton({ label = "Add product" }: { label?: string }) {
  const { openAdd } = useProductDialogs();
  return (
    <Button onClick={openAdd} className="shadow-upload">
      <Add aria-hidden /> {label}
    </Button>
  );
}

const menuItem =
  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[9px] text-left text-sm leading-5 font-medium outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/40 [&_svg]:size-4 [&_svg]:shrink-0";

/** Grid card (Figma "Product card"): click opens the details, "…" opens Edit / Delete. */
export function ProductCard({ product }: { product: Product }) {
  const { openDetails, openEdit, openDelete } = useProductDialogs();

  return (
    <article className="relative flex flex-col rounded-2xl border border-border bg-background p-2 transition-[border-color,box-shadow] focus-within:border-primary/40 hover:border-primary/40 hover:shadow-card">
      <ProductImage src={product.imageUrl} alt="" sizes="(min-width: 1280px) 25vw, 50vw" className="w-full" />
      <div className="flex flex-col gap-3 pt-3 pr-1 pb-1.5 pl-2">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-sans text-[15px] leading-[22px] font-semibold text-foreground">
              <button
                type="button"
                onClick={() => openDetails(product)}
                className="text-left outline-none after:absolute after:inset-0 after:rounded-2xl after:content-[''] focus-visible:after:ring-3 focus-visible:after:ring-ring/40"
              >
                {product.name}
              </button>
            </h3>
            <p className="truncate text-[13px] leading-[19.5px] text-muted-foreground">
              {product.category} · {product.description}
            </p>
          </div>
          <div className="relative z-10">
            <Popover
              label={`More actions for ${product.name}`}
              trigger={<More aria-hidden className="size-[18px]" />}
              triggerClassName="size-8 rounded-[10px] text-foreground-soft transition-colors hover:bg-primary-soft"
              panelClassName="w-[174px] rounded-[14px] p-1.5 shadow-menu"
            >
              {(close) => (
                <div role="menu" className="flex flex-col">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      close();
                      openEdit(product);
                    }}
                    className={cn(menuItem, "text-foreground hover:bg-primary-soft")}
                  >
                    <Edit2 aria-hidden /> Edit product
                  </button>
                  <div className="px-1 py-[3px]">
                    <div className="h-px bg-border" />
                  </div>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      close();
                      openDelete(product);
                    }}
                    className={cn(menuItem, "text-danger hover:bg-danger-soft")}
                  >
                    <Trash aria-hidden /> Delete product
                  </button>
                </div>
              )}
            </Popover>
          </div>
        </div>
        <p className="font-heading text-xl leading-[26px] font-semibold text-foreground">
          {formatPrice(product.price, "USD")}
        </p>
      </div>
    </article>
  );
}

/** Thumbnail + name + description cell of the list view. Opens the details. */
export function ProductCellButton({ product }: { product: Product }) {
  const { openDetails } = useProductDialogs();
  return (
    <button
      type="button"
      onClick={() => openDetails(product)}
      className="group flex w-full min-w-0 items-center gap-3 rounded-lg text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
    >
      <ProductImage src={product.imageUrl} alt="" sizes="64px" className="w-16 rounded-lg" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm leading-[21px] font-semibold text-foreground group-hover:text-primary">
          {product.name}
        </span>
        <span className="block truncate text-[13px] leading-[19.5px] text-muted-foreground">{product.description}</span>
      </span>
    </button>
  );
}

/** Edit and delete icon buttons at the end of a list row. */
export function ProductRowActions({ product }: { product: Product }) {
  const { openEdit, openDelete } = useProductDialogs();
  return (
    <div className="flex items-center justify-end gap-1">
      <Button variant="ghost" size="icon" aria-label={`Edit ${product.name}`} onClick={() => openEdit(product)}>
        <Edit2 aria-hidden />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Delete ${product.name}`}
        onClick={() => openDelete(product)}
        className="hover:bg-danger-soft hover:text-danger active:bg-danger-soft"
      >
        <Trash aria-hidden />
      </Button>
    </div>
  );
}
