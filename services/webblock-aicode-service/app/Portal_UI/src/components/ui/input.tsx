import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Base field look (dashboard pill). Shared with `select.tsx` so inputs and selects match. */
export const fieldClasses =
  "h-12 w-full rounded-full border border-input bg-background px-4 text-[15px] text-foreground placeholder:text-placeholder outline-none transition-[border-color,box-shadow] focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:bg-muted disabled:text-placeholder aria-invalid:border-danger aria-invalid:ring-danger/20";

/** `lg` = the taller auth-screen field (Figma auth pages): 52px, 14px corners, soft shadow. */
const sizeClasses = {
  md: "",
  lg: "h-[52px] rounded-[14px] px-3.5 shadow-button focus-visible:ring-4 focus-visible:ring-primary/16 aria-invalid:focus-visible:ring-danger/16",
} as const;

type InputProps = Omit<ComponentProps<"input">, "size"> & {
  /** `md` (default) for app screens, `lg` for the auth pages. */
  size?: keyof typeof sizeClasses;
  /** Icon shown inside the field, on the left. */
  icon?: ReactNode;
  /** Extra classes for the icon holder, e.g. to move the icon closer to the edge. */
  iconClassName?: string;
};

/**
 * WebBlock text field (Figma "Input"). Pair it with a `<label htmlFor>`; set `aria-invalid`
 * when the field has an error so the border turns red.
 *
 * @example
 * <Input id="search" name="q" icon={<SearchNormal1 />} placeholder="Search" />
 * <Input id="email" name="email" type="email" size="lg" aria-invalid={!!error} />
 */
export function Input({ className, size = "md", icon, iconClassName, type = "text", ...props }: InputProps) {
  const classes = cn(fieldClasses, sizeClasses[size], icon && "pl-11", className);
  if (!icon) return <input type={type} className={classes} {...props} />;
  return (
    <div className="relative">
      <span
        aria-hidden
        className={cn("pointer-events-none absolute inset-y-0 left-4 flex items-center text-muted-foreground [&_svg]:size-[18px]", iconClassName)}
      >
        {icon}
      </span>
      <input type={type} className={classes} {...props} />
    </div>
  );
}
