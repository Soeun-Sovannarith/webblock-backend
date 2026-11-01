import type { ComponentProps } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * WebBlock button (Figma "Button" + "Icon button").
 * primary = main action (one per view) · secondary = neutral · soft = secondary brand action
 * (Edit, Use template) · ghost = low emphasis · dark = high contrast · danger = destructive ·
 * white = on coloured backgrounds · outline / tonal = landing page (Figma "LP/Button").
 */
export const buttonVariants = cva(
  [
    "relative inline-flex shrink-0 items-center justify-center rounded-full font-semibold whitespace-nowrap select-none",
    // Tailwind 4 moves buttons with the `translate`/`scale` properties (not `transform`), so list those,
    // and `filter` for the plan-coloured buttons that darken with `brightness-*`.
    "transition-[background-color,color,border-color,box-shadow,translate,scale,filter] duration-150 ease-out",
    "outline-none focus-visible:ring-3 focus-visible:ring-ring/40 focus-visible:ring-offset-2",
    "motion-safe:active:scale-97",
    "disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-brand text-primary-foreground shadow-button hover:bg-brand-strong hover:shadow-button-hover active:bg-brand-dark motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0",
        secondary:
          "border border-border bg-background text-foreground shadow-button hover:border-primary/40 hover:bg-muted hover:shadow-button-hover active:bg-primary-soft motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0",
        soft: "bg-brand-soft text-brand-strong hover:bg-brand/20 active:bg-brand/25",
        ghost:
          "text-foreground-soft hover:bg-primary-soft hover:text-foreground active:bg-brand-soft",
        dark: "bg-brand-dark text-primary-foreground shadow-button hover:bg-brand-dark/90 hover:shadow-button-hover motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0",
        danger:
          "bg-danger-soft text-danger hover:bg-danger hover:text-primary-foreground active:bg-danger/90",
        white:
          "bg-background text-brand-strong shadow-button hover:bg-primary-soft hover:shadow-button-hover motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0",
        outline:
          "border border-border-strong text-foreground hover:border-primary hover:bg-primary-soft active:bg-brand-soft",
        tonal: "bg-brand-soft text-brand-deep hover:bg-brand/20 active:bg-brand/25",
      },
      size: {
        sm: "h-8 gap-2 px-3 text-[13px] [&_svg]:size-[15px]",
        md: "h-10 gap-2 px-[18px] text-sm [&_svg]:size-4",
        lg: "h-[50px] gap-2.5 px-6 text-[15px] [&_svg]:size-[18px]",
        // Landing "LP/Button" sizes: medium-weight 16px label.
        "lp-md": "h-11 gap-2 px-5 text-base font-medium [&_svg]:size-[18px]",
        "lp-lg": "h-[52px] gap-2.5 px-7 text-base font-medium [&_svg]:size-5",
        "icon-sm": "size-8 [&_svg]:size-4",
        icon: "size-9 [&_svg]:size-[18px]",
        "icon-lg": "size-10 [&_svg]:size-5",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    /** Render the child element (e.g. a `<Link>`) with button styles instead of a `<button>`. */
    asChild?: boolean;
    /** Shows a spinner and blocks clicks. Ignored with `asChild`. */
    loading?: boolean;
  };

/**
 * `type` defaults to "button" so a button inside a form never submits by accident —
 * pass `type="submit"` explicitly. Icon-only buttons (`size="icon…"`) need an `aria-label`.
 *
 * @example
 * <Button variant="soft" size="sm"><Edit2 /> Edit</Button>
 * <Button asChild><Link href="/websites/new">New website</Link></Button>
 * <Button size="icon" variant="ghost" aria-label="More actions"><More /></Button>
 */
export function Button({
  className,
  variant,
  size,
  asChild = false,
  loading = false,
  disabled,
  type = "button",
  children,
  ...props
}: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);

  if (asChild) {
    return (
      <Slot className={classes} {...props}>
        {children}
      </Slot>
    );
  }

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="size-[1.1em] animate-spin rounded-full border-2 border-current border-r-transparent"
        />
      )}
      {children}
    </button>
  );
}
