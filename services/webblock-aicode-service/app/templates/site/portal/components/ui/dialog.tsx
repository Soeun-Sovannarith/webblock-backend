"use client";

import type { ComponentProps } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { Add } from "iconsax-reactjs";
import { cn } from "@/portal/lib/utils";

// shadcn/ui dialog primitives. Use `common/modal` or `common/confirm-dialog` for screens.

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogTitle = DialogPrimitive.Title;
export const DialogDescription = DialogPrimitive.Description;

export function DialogContent({
  className,
  children,
  showClose = true,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content> & { showClose?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-overlay motion-safe:animate-fade-in motion-safe:data-[state=closed]:animate-fade-out" />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-3xl border border-border bg-background shadow-popover outline-none motion-safe:animate-pop-in motion-safe:data-[state=closed]:animate-pop-out",
          className,
        )}
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute top-4 right-4 z-10 grid size-8 place-items-center rounded-full border border-border bg-background text-foreground-soft shadow-button transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/40"
          >
            <Add aria-hidden className="size-5 rotate-45" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
