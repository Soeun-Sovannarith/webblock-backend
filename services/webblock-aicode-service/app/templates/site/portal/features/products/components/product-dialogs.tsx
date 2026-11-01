"use client";

import { createContext, useContext, useState, useTransition, type ReactNode } from "react";
import { Edit2, Trash } from "iconsax-reactjs";
import { deleteProductAction } from "../actions";
import { ConfirmDialog } from "@/portal/components/common/confirm-dialog";
import { Modal } from "@/portal/components/common/modal";
import { ProductImage } from "@/portal/components/common/product-image";
import { toast } from "@/portal/components/common/toast";
import { Button } from "@/portal/components/ui/button";
import { formatPrice } from "@/portal/lib/format";
import type { Product } from "../types";
import { ProductForm } from "./product-form";
import { CategoryChip, ProductDialogHeader } from "./product-parts";

type Dialog =
  | { kind: "add" }
  | { kind: "details" | "edit" | "delete"; product: Product }
  | null;

type ProductDialogs = {
  openAdd: () => void;
  openDetails: (product: Product) => void;
  openEdit: (product: Product) => void;
  openDelete: (product: Product) => void;
};

const ProductDialogsContext = createContext<ProductDialogs | null>(null);

/** Opens the product popups (details, add, edit, delete) from any card, row or button inside the provider. */
export function useProductDialogs(): ProductDialogs {
  const value = useContext(ProductDialogsContext);
  if (!value) throw new Error("useProductDialogs must be used inside <ProductDialogsProvider>.");
  return value;
}

/** Holds which product popup is open. Cards and rows stay Server-rendered children. */
export function ProductDialogsProvider({
  websiteId,
  domain,
  categories,
  children,
}: {
  websiteId: string;
  domain: string | null;
  categories: string[];
  children: ReactNode;
}) {
  const [dialog, setDialog] = useState<Dialog>(null);
  const [deleting, startDelete] = useTransition();
  const close = () => setDialog(null);

  const api: ProductDialogs = {
    openAdd: () => setDialog({ kind: "add" }),
    openDetails: (product) => setDialog({ kind: "details", product }),
    openEdit: (product) => setDialog({ kind: "edit", product }),
    openDelete: (product) => setDialog({ kind: "delete", product }),
  };

  const product = dialog && dialog.kind !== "add" ? dialog.product : null;
  const shownOn = domain ? `Shown as a Product Card on ${domain}` : "Shown as a Product Card on your website";

  function remove(target: Product) {
    startDelete(async () => {
      const result = await deleteProductAction(websiteId, target.id);
      close();
      if (result.ok) toast.success("Product deleted", result.message);
      else toast.error("Could not delete the product", result.message);
    });
  }

  return (
    <ProductDialogsContext value={api}>
      {children}

      <Modal
        open={dialog?.kind === "details"}
        onClose={close}
        label="Product details"
        className="max-w-[616px] rounded-[20px]"
      >
        {product && (
          <ProductDetails
            product={product}
            subtitle={shownOn}
            onClose={close}
            onEdit={() => api.openEdit(product)}
            onDelete={() => api.openDelete(product)}
          />
        )}
      </Modal>

      <Modal
        open={dialog?.kind === "add" || dialog?.kind === "edit"}
        onClose={close}
        label={dialog?.kind === "edit" ? "Edit product" : "Add product"}
        className="max-w-[616px] rounded-[20px]"
      >
        {dialog?.kind === "add" && (
          <ProductForm websiteId={websiteId} categories={categories} product={null} onDone={close} />
        )}
        {dialog?.kind === "edit" && (
          <ProductForm websiteId={websiteId} categories={categories} product={dialog.product} onDone={close} />
        )}
      </Modal>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onClose={close}
        onConfirm={() => product && remove(product)}
        loading={deleting}
        icon={<Trash />}
        title={`Delete “${product?.name ?? ""}”?`}
        description="It will be removed from your website the next time you deploy. This can’t be undone."
        confirmLabel="Delete product"
      />
    </ProductDialogsContext>
  );
}

function ProductDetails({
  product,
  subtitle,
  onClose,
  onEdit,
  onDelete,
}: {
  product: Product;
  subtitle: string;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="p-[22px]">
      <ProductDialogHeader title="Product details" subtitle={subtitle} onClose={onClose} />

      <div className="grid gap-[22px] border-b border-border py-4 sm:grid-cols-[292px_minmax(0,1fr)]">
        <ProductImage src={product.imageUrl} alt={product.name} sizes="292px" className="w-full rounded-xl" />
        <div className="flex min-w-0 flex-col items-start">
          <CategoryChip category={product.category} />
          <h3 className="pt-3 text-xl leading-[26px] font-semibold break-words text-foreground">{product.name}</h3>
          <p className="pt-2 text-[13px] leading-[18px] text-muted-foreground">{product.description}</p>
          <p className="mt-auto pt-4 font-heading text-[26px] leading-8 font-semibold text-foreground">
            {formatPrice(product.price, "USD")}
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 pt-4">
        <Button variant="ghost" size="sm" className="text-danger hover:bg-danger-soft hover:text-danger active:bg-danger-soft" onClick={onDelete}>
          <Trash aria-hidden /> Delete
        </Button>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button size="sm" onClick={onEdit}>
            <Edit2 aria-hidden /> Edit product
          </Button>
        </div>
      </div>
    </div>
  );
}
