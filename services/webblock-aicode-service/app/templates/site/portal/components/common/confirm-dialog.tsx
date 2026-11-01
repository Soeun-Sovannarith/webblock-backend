"use client";

import type { ReactNode } from "react";
import { Danger } from "iconsax-reactjs";
import { Button } from "@/portal/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/portal/components/ui/dialog";
import { cn } from "@/portal/lib/utils";

type ConfirmDialogProps = {
  open: boolean;
  /** Called with `false` when the user cancels or presses Escape. */
  onOpenChange?: (open: boolean) => void;
  /** Same as `onOpenChange(false)`; either one is enough. */
  onClose?: () => void;
  onConfirm: () => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  icon?: ReactNode;
  /** Keeps the dialog open with a spinner while the action runs. */
  pending?: boolean;
  /** Same as `pending`. */
  loading?: boolean;
  /** `danger` (default) for destructive actions; `primary` / `brand` for a neutral "Continue?" question. */
  tone?: "danger" | "primary" | "brand";
  /** `soft` = centred, extra-round card: light blue gradient header, icon in a halo, full-width buttons (log out, suspend). */
  look?: "default" | "soft";
};

/**
 * Small "Are you sure?" popup (Figma "Suspend website (confirm)"): icon tile, title, explanation,
 * Cancel + confirm. Stays open while `pending` so the user sees the action finish.
 *
 * @example
 * <ConfirmDialog
 *   open={open} onOpenChange={setOpen} pending={pending}
 *   title="Suspend this user?" confirmLabel="Suspend user"
 *   description="They can't sign in until you reactivate them."
 *   onConfirm={() => startTransition(async () => { … })}
 * />
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel,
  icon,
  pending,
  loading,
  tone = "danger",
  look = "default",
}: ConfirmDialogProps) {
  const busy = pending ?? loading ?? false;
  const danger = tone === "danger";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        onOpenChange?.(next);
        if (!next) onClose?.();
      }}
    >
      {look === "soft" ? (
        <DialogContent showClose={false} className="max-w-[340px] rounded-[28px] p-0 text-center">
          {/* Header: light blue gradient, a faint dot grid fading out around the icon, icon in a halo. */}
          <div className="relative flex justify-center overflow-hidden bg-linear-to-b from-brand-soft to-background pt-8 pb-5">
            <span
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-[radial-gradient(var(--color-decor-dot)_1.2px,transparent_1.2px)] bg-size-[12px_12px] mask-radial-from-0% mask-radial-to-65% mask-radial-at-center opacity-60"
            />
            <span
              aria-hidden
              className={cn(
                "relative grid size-[76px] place-items-center rounded-full ring-1",
                danger ? "bg-danger-soft/70 ring-danger/10" : "bg-background/70 ring-brand-border/40",
              )}
            >
              {/* Two rings rippling out from the halo on a loop, the second half a beat behind. */}
              <span
                className={cn(
                  "absolute inset-0 rounded-full border motion-safe:animate-halo-ripple motion-reduce:hidden",
                  danger ? "border-danger/30" : "border-brand/30",
                )}
              />
              <span
                className={cn(
                  "absolute inset-0 rounded-full border [animation-delay:1.2s] motion-safe:animate-halo-ripple motion-reduce:hidden",
                  danger ? "border-danger/30" : "border-brand/30",
                )}
              />
              <span
                className={cn(
                  "relative grid size-14 place-items-center rounded-full bg-background shadow-card motion-safe:animate-halo-float [&_svg]:size-6",
                  danger ? "text-danger" : "text-brand",
                )}
              >
                {icon ?? <Danger />}
              </span>
            </span>
          </div>

          <div className="px-6 pb-6">
            <DialogTitle className="text-lg leading-6 font-semibold text-foreground">{title}</DialogTitle>
            <DialogDescription className="mx-auto mt-2 max-w-[272px] text-[13px] leading-5 text-muted-foreground">
              {description}
            </DialogDescription>
            {/* Both buttons: same height, width and text size (borderless, so neither looks bigger). */}
            <div className="mt-6 grid grid-cols-2 gap-2">
              <DialogClose asChild>
                <Button
                  variant="ghost"
                  className="h-9 bg-muted px-3 text-[13px] text-foreground-soft hover:bg-neutral-soft"
                  disabled={busy}
                >
                  Cancel
                </Button>
              </DialogClose>
              <Button
                variant={danger ? "danger" : "primary"}
                className={cn(
                  "h-9 px-3 text-[13px] shadow-none",
                  danger && "bg-danger text-primary-foreground hover:bg-danger/90",
                )}
                loading={busy}
                onClick={onConfirm}
              >
                {confirmLabel}
              </Button>
            </div>
          </div>
        </DialogContent>
      ) : (
        <DialogContent showClose={false} className="max-w-[380px] rounded-[20px] p-[22px]">
          <span
            aria-hidden
            className={cn(
              "mb-3 grid size-10 place-items-center rounded-xl [&_svg]:size-5",
              danger ? "bg-danger-soft text-danger" : "bg-brand-soft text-brand",
            )}
          >
            {icon ?? <Danger />}
          </span>
          <DialogTitle className="text-[17px] font-semibold text-foreground">{title}</DialogTitle>
          <DialogDescription className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
            {description}
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-2">
            <DialogClose asChild>
              <Button variant="ghost" size="sm" className="text-foreground-soft shadow-button" disabled={busy}>
                Cancel
              </Button>
            </DialogClose>
            <Button variant={danger ? "danger" : "primary"} size="sm" loading={busy} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}
