"use client";

import { useActionState, useEffect, useRef, useState, useTransition } from "react";
import { Export } from "iconsax-reactjs";
import { createProductAction, updateProductAction } from "../actions";
import { ProductImage } from "@/components/common/product-image";
import { toast } from "@/components/common/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectField } from "@/components/common/select-field";
import { Textarea } from "@/components/ui/textarea";
import { checkImage, PRODUCT_IMAGE_RULE } from "@/lib/image-rules";
import { cn } from "@/lib/utils";
import type { Product } from "../types";
import { ProductDialogHeader } from "./product-parts";

const fieldClasses = "h-[38px] rounded-xl px-3 text-sm";
const labelClasses = "block pb-1.5 text-[13px] leading-5 font-semibold text-foreground";

/** "Add product" / "Edit product" popup body: photo on the left, fields on the right. */
export function ProductForm({
  websiteId,
  categories,
  product,
  onDone,
}: {
  websiteId: string;
  categories: string[];
  /** `null` adds a new product. */
  product: Product | null;
  onDone: () => void;
}) {
  const action = product
    ? updateProductAction.bind(null, websiteId, product.id)
    : createProductAction.bind(null, websiteId);
  const [state, formAction, saving] = useActionState(action, null);
  const [, startTransition] = useTransition();
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(product?.imageUrl ?? null);
  const [imageError, setImageError] = useState<string | null>(null);

  const handled = useRef(state);
  useEffect(() => {
    // Each result is announced once, even when the parent re-renders with a new `onDone`.
    if (!state || handled.current === state) return;
    handled.current = state;
    if (state.ok) {
      toast.success(product ? "Changes saved" : "Product added", state.message);
      onDone();
    } else if (!state.fieldErrors) {
      toast.error("Could not save the product", state.message);
    }
  }, [state, product, onDone]);

  // Free the object URL of a picked photo when it is replaced or the popup closes.
  useEffect(() => {
    if (!preview?.startsWith("blob:")) return;
    return () => URL.revokeObjectURL(preview);
  }, [preview]);

  function choose(file: File | undefined) {
    if (!file) return;
    const problem = checkImage(file, PRODUCT_IMAGE_RULE);
    setImageError(problem);
    if (problem) {
      if (inputRef.current) inputRef.current.value = "";
      return;
    }
    setPreview(URL.createObjectURL(file));
  }

  const values = state && !state.ok ? state.values : undefined;
  const errors = state && !state.ok ? state.fieldErrors : undefined;
  const error = (name: string) => errors?.[name]?.[0];
  const photoError = imageError ?? error("image");

  return (
    <form
      noValidate
      className="p-[22px]"
      // Submit without React's automatic form reset so the picked photo survives a validation error.
      onSubmit={(event) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        startTransition(() => formAction(formData));
      }}
    >
      <ProductDialogHeader
        title={product ? "Edit product" : "Add product"}
        subtitle={product ? product.name : "New product"}
        onClose={onDone}
      />

      <div className="grid gap-5 border-b border-border py-4 sm:grid-cols-[226px_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            aria-describedby="product-image-hint"
            className={cn(
              "group relative flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-xl border border-dashed text-sm font-semibold text-brand-strong outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/40",
              photoError ? "border-danger bg-danger-soft" : "border-dashed-line bg-dropzone hover:bg-primary-soft",
              preview && "border-solid border-border",
            )}
          >
            {preview ? (
              <>
                <ProductImage src={preview} alt="" sizes="226px" className="absolute inset-0 w-full rounded-none" />
                <span className="absolute right-2 bottom-2 rounded-full bg-background px-3 py-1 text-xs shadow-button">
                  Change photo
                </span>
              </>
            ) : (
              <>
                <span aria-hidden className="flex size-8 items-center justify-center rounded-lg bg-brand-soft">
                  <Export className="size-4" />
                </span>
                Add a photo
              </>
            )}
          </button>
          <input
            ref={inputRef}
            type="file"
            name="image"
            accept={PRODUCT_IMAGE_RULE.types.join(",")}
            className="sr-only"
            tabIndex={-1}
            aria-label="Product photo"
            onChange={(event) => choose(event.target.files?.[0])}
          />
          <p
            id="product-image-hint"
            role={photoError ? "alert" : undefined}
            className={cn("text-[11px] leading-4", photoError ? "text-danger" : "text-muted-foreground")}
          >
            {photoError ?? `${PRODUCT_IMAGE_RULE.label}. Landscape 4:3, as shown on your website.`}
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <div>
            <label htmlFor="product-name" className={labelClasses}>
              Product name
            </label>
            <Input
              id="product-name"
              name="name"
              autoFocus
              placeholder="e.g. Sunflower bunch"
              defaultValue={values?.name ?? product?.name}
              aria-invalid={!!error("name")}
              aria-describedby={error("name") ? "product-name-error" : undefined}
              className={fieldClasses}
            />
            <FieldError id="product-name-error" message={error("name")} />
          </div>

          <div>
            <label htmlFor="product-category" className={labelClasses}>
              Category
            </label>
            <SelectField
              id="product-category"
              name="category"
              defaultValue={values?.category ?? product?.category ?? ""}
              placeholder="Choose a category"
              options={categories.map((category) => ({ value: category, label: category }))}
              aria-invalid={!!error("category")}
              aria-describedby={error("category") ? "product-category-error" : undefined}
              className={fieldClasses}
            />
            <FieldError id="product-category-error" message={error("category")} />
          </div>

          <div>
            <label htmlFor="product-description" className={labelClasses}>
              Description
            </label>
            <Textarea
              id="product-description"
              name="description"
              rows={3}
              placeholder="Tell customers what it is, what’s included and how to care for it."
              defaultValue={values?.description ?? product?.description}
              aria-invalid={!!error("description")}
              aria-describedby={error("description") ? "product-description-error" : undefined}
              className="min-h-[92px] resize-none rounded-xl px-3 py-2.5 text-sm"
            />
            <FieldError id="product-description-error" message={error("description")} />
          </div>

          <div>
            <label htmlFor="product-price" className={labelClasses}>
              Price
            </label>
            <Input
              id="product-price"
              name="price"
              inputMode="decimal"
              placeholder="0.00"
              icon={<span className="text-sm">$</span>}
              iconClassName="left-3"
              defaultValue={values?.price ?? product?.price}
              aria-invalid={!!error("price")}
              aria-describedby={error("price") ? "product-price-error" : undefined}
              className={cn(fieldClasses, "pl-7")}
            />
            <FieldError id="product-price-error" message={error("price")} />
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 pt-4">
        <Button variant="secondary" size="sm" onClick={onDone} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" size="sm" className="shadow-upload" loading={saving}>
          {product ? "Save changes" : "Save product"}
        </Button>
      </div>
    </form>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="pt-1 text-xs text-danger">
      {message}
    </p>
  );
}
