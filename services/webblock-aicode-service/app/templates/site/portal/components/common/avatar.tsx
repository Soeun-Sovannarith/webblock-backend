import Image from "next/image";
import { Global } from "iconsax-reactjs";
import { cn } from "@/portal/lib/utils";

const SIZE = {
  sm: { box: "size-7 text-[11px]", px: 28 },
  md: { box: "size-9 text-[13px]", px: 36 },
  lg: { box: "size-[72px] text-xl", px: 72 },
} as const;

const VARIANT = {
  /** Soft blue gradient, dark-blue initials (app sidebar, settings). */
  soft: "bg-linear-[140deg] from-brand-soft to-violet-border text-brand-deep",
  /** Solid brand circle, white initials, white ring (admin tables and menus). */
  solid: "bg-brand text-primary-foreground ring-2 ring-background",
} as const;

/** "Sokha Chan" → "SC" · "Admin" → "AD" */
function initialsOf(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return parts
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

/** Round photo, or the person's initials when `src` is null. */
export function Avatar({
  name,
  src,
  initials,
  size = "md",
  variant = "soft",
  className,
}: {
  name: string;
  src: string | null;
  /** Letters shown without a photo, e.g. "SV". Defaults to the first letters of `name`. */
  initials?: string;
  size?: keyof typeof SIZE;
  variant?: keyof typeof VARIANT;
  /** Extra classes, e.g. a custom size "size-10 text-sm". */
  className?: string;
}) {
  const s = SIZE[size];
  return (
    <span
      data-avatar
      className={cn(
        "relative inline-grid shrink-0 place-items-center overflow-hidden rounded-full font-bold",
        VARIANT[variant],
        s.box,
        className,
      )}
    >
      {src ? (
        <Image src={src} alt="" width={s.px} height={s.px} unoptimized className="size-full object-cover" />
      ) : (
        <span aria-hidden>{initials ?? initialsOf(name)}</span>
      )}
      <span className="sr-only">{name}</span>
    </span>
  );
}

/** Small rounded-square website thumbnail; a globe placeholder (plus "No preview" when large) when there is no image yet. */
export function Thumbnail({
  src,
  size = "md",
  className,
}: {
  src: string | null;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const px = size === "sm" ? 28 : size === "md" ? 36 : 120;
  return (
    <span
      data-thumb
      className={cn(
        "relative inline-block shrink-0 overflow-hidden border border-border bg-muted",
        size === "lg" ? "aspect-[4/3] w-[120px] rounded-2xl" : "rounded-lg",
        size === "sm" && "size-7",
        size === "md" && "size-9",
        className,
      )}
    >
      {src ? (
        <Image src={src} alt="" width={px} height={size === "lg" ? 90 : px} className="size-full object-cover" />
      ) : (
        <span className="flex size-full flex-col items-center justify-center gap-1 bg-primary-soft text-primary/70">
          <Global
            aria-hidden
            className={cn(size === "sm" ? "size-3.5" : size === "md" ? "size-[18px]" : "size-7")}
          />
          {size === "lg" && <span className="text-[11px] font-medium text-muted-foreground">No preview</span>}
        </span>
      )}
    </span>
  );
}
