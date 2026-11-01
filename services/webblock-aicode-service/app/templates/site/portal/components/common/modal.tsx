"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/portal/components/ui/dialog";
import { PortalContainerContext } from "@/portal/components/ui/portal-container";
import { raiseToastsInto } from "@/portal/components/common/toast";
import { cn } from "@/portal/lib/utils";

const SIZE = {
  sm: "max-w-[400px]",
  md: "max-w-[520px]",
  lg: "max-w-[600px]",
  xl: "max-w-[680px]",
} as const;

/** Bare popup: the content draws its own heading and close button (app screens). */
type BareModalProps = {
  open: boolean;
  onClose: () => void;
  /** Accessible name of the dialog. */
  label: string;
  children: ReactNode;
  /** Width and other layout classes, e.g. "max-w-lg". */
  className?: string;
};

/** Titled popup: heading, ✕ and an optional footer are built in (admin screens). */
type TitledModalProps = {
  /** Accessible name. Shown as the heading unless `hideTitle` (when the body has its own heading). */
  title: string;
  description?: string;
  hideTitle?: boolean;
  size?: keyof typeof SIZE;
  /** "right" = a full-height panel on the right (e.g. Publish flow). */
  side?: "center" | "right";
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
} & (
  | {
      /** URL-driven popup: always open, closing navigates here (e.g. the list without `?user=`). */
      closeHref: string;
      open?: never;
      onOpenChange?: never;
    }
  | { closeHref?: never; open: boolean; onOpenChange: (open: boolean) => void }
);

type ModalProps = BareModalProps | TitledModalProps;

/**
 * Every popup (Figma "Plan details dialog" style): centered card, blurred backdrop.
 * Two forms, chosen by the props:
 * - `open` + `onClose` + `label`: bare card, the children draw their own heading and close button.
 * - `title` (+ `closeHref`, or `open` + `onOpenChange`): heading, ✕ and `footer` built in.
 *   Admin row details are URL-driven (`closeHref`), so a detail can be linked to and Back closes it.
 *
 * @example
 * <Modal open={open} onClose={() => setOpen(false)} label="Edit product">…</Modal>
 * {userId && <Modal title={user.fullName} hideTitle closeHref={tableHref(searchParams, { user: null })}>…</Modal>}
 */
export function Modal(props: ModalProps) {
  return "label" in props ? <BareModal {...props} /> : <TitledModal {...props} />;
}

/**
 * Native `<dialog>`: focus trap, Escape and the blurred backdrop come from the browser.
 * Children mount while open and stay through the exit animation, then unmount, so forms reset every time.
 */
function BareModal({ open, onClose, label, children, className }: BareModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  // Dropdowns inside the modal portal into the <dialog>; in <body> they would sit under its top layer.
  const [dialogEl, setDialogEl] = useState<HTMLDialogElement | null>(null);
  // `close` events caused by `open` turning false are not the user closing the dialog.
  const closedByProp = useRef(false);
  // Keeps the content on screen while the card fades out (otherwise an empty card fades).
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) {
      closedByProp.current = true;
      dialog.close();
    }
    if (!open) {
      // Matches the `dialog-motion` exit in globals.css, plus a frame of slack.
      const timer = setTimeout(() => setMounted(false), 160);
      return () => clearTimeout(timer);
    }
    // Toasts shown while the dialog is open would sit behind it: move them into it.
    return raiseToastsInto(dialog);
  }, [open]);

  return (
    <dialog
      ref={(el) => {
        ref.current = el;
        setDialogEl(el);
      }}
      aria-label={label}
      onClose={() => {
        if (closedByProp.current) closedByProp.current = false;
        else onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      className={cn(
        "m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-md overflow-hidden rounded-[18px] border border-border bg-background p-0 text-foreground shadow-dialog",
        "dialog-motion backdrop:bg-scrim backdrop:backdrop-blur-[7px]",
        className,
      )}
    >
      <PortalContainerContext value={dialogEl}>{(open || mounted) && children}</PortalContainerContext>
    </dialog>
  );
}

function TitledModal({
  title,
  description,
  hideTitle = false,
  size = "md",
  side = "center",
  children,
  footer,
  className,
  closeHref,
  open,
  onOpenChange,
}: TitledModalProps) {
  const router = useRouter();
  const routed = closeHref !== undefined;
  // URL-driven popups close locally first so the exit animation can play, then update the URL.
  const [routedOpen, setRoutedOpen] = useState(true);

  return (
    <Dialog
      open={routed ? routedOpen : open}
      onOpenChange={(next) => {
        if (routed) {
          if (!next) {
            setRoutedOpen(false);
            const exitMs = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 150;
            setTimeout(() => router.push(closeHref, { scroll: false }), exitMs);
          }
        } else {
          onOpenChange?.(next);
        }
      }}
    >
      <DialogContent
        className={cn(
          side === "right"
            ? "top-0 right-0 left-auto h-dvh max-h-dvh w-[calc(100vw-16px)] max-w-[1220px] translate-x-0 translate-y-0 rounded-l-3xl rounded-r-none md:w-[70vw] motion-safe:animate-pop-in motion-safe:data-[state=closed]:animate-pop-out"
            : SIZE[size],
          className,
        )}
      >
        <div className={cn("min-h-0 flex-1 overflow-y-auto", !hideTitle && "px-6 pt-6")}>
          <DialogTitle
            className={cn(hideTitle ? "sr-only" : "pr-10 text-[22px] leading-7 font-semibold text-foreground")}
          >
            {title}
          </DialogTitle>
          {description ? (
            <DialogDescription className={cn(hideTitle ? "sr-only" : "mt-1 text-[13px] text-muted-foreground")}>
              {description}
            </DialogDescription>
          ) : (
            <DialogDescription className="sr-only">{title}</DialogDescription>
          )}
          {children}
        </div>
        {footer && <div className="flex justify-end gap-2.5 border-t border-border px-6 py-4">{footer}</div>}
      </DialogContent>
    </Dialog>
  );
}
