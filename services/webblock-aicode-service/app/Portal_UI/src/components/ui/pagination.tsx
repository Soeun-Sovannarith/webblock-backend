import type { ComponentProps } from "react";
import Link from "next/link";
import { ArrowLeft2, ArrowRight2 } from "iconsax-reactjs";
import { cn } from "@/lib/utils";

// shadcn/ui pagination primitives, styled to Figma "Pagination / Page" and "Pagination / Nav button".
// Every page is a link, so the current page lives in the URL.

export function Pagination({ className, ...props }: ComponentProps<"nav">) {
  return <nav aria-label="Pagination" className={cn("flex items-center", className)} {...props} />;
}

export function PaginationContent({ className, ...props }: ComponentProps<"ul">) {
  return <ul className={cn("flex items-center gap-1.5", className)} {...props} />;
}

export function PaginationItem(props: ComponentProps<"li">) {
  return <li {...props} />;
}

const itemBase =
  "inline-flex items-center justify-center rounded-full text-sm leading-5 font-medium text-foreground-soft outline-none transition-[background-color,color,box-shadow,transform] duration-150 focus-visible:ring-3 focus-visible:ring-ring/40 motion-safe:active:scale-95";

export function PaginationLink({
  className,
  isActive,
  ...props
}: ComponentProps<typeof Link> & { isActive?: boolean }) {
  return (
    <Link
      aria-current={isActive ? "page" : undefined}
      className={cn(
        itemBase,
        "size-9",
        isActive
          ? "bg-brand font-semibold text-primary-foreground shadow-page-active"
          : "hover:bg-primary-soft hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

const navBase = "h-9 gap-1.5 bg-primary-soft [&_svg]:size-4";

/** Without `href` it renders disabled (first/last page). */
export function PaginationPrevious({ href }: { href?: string }) {
  const content = (
    <>
      <ArrowLeft2 aria-hidden />
      Previous
    </>
  );
  return href ? (
    <Link href={href} className={cn(itemBase, navBase, "pr-3.5 pl-2.5 hover:bg-brand-soft hover:text-foreground")}>
      {content}
    </Link>
  ) : (
    <span aria-disabled="true" className={cn(itemBase, navBase, "pr-3.5 pl-2.5 opacity-45")}>
      {content}
    </span>
  );
}

/** Without `href` it renders disabled (first/last page). */
export function PaginationNext({ href }: { href?: string }) {
  const content = (
    <>
      Next
      <ArrowRight2 aria-hidden />
    </>
  );
  return href ? (
    <Link href={href} className={cn(itemBase, navBase, "pr-2.5 pl-3.5 hover:bg-brand-soft hover:text-foreground")}>
      {content}
    </Link>
  ) : (
    <span aria-disabled="true" className={cn(itemBase, navBase, "pr-2.5 pl-3.5 opacity-45")}>
      {content}
    </span>
  );
}

export function PaginationEllipsis({ className, ...props }: ComponentProps<"span">) {
  return (
    <span aria-hidden className={cn("flex size-9 items-center justify-center text-[13px] text-muted-foreground", className)} {...props}>
      …
    </span>
  );
}
