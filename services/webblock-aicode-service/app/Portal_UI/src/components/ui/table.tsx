import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

// shadcn/ui table primitives, styled to the Figma admin table. Use `common/data-table` for screens.

/** `containerClassName` styles the scrolling wrapper, e.g. `min-h-0` so it can scroll inside a fixed-height card. */
export function Table({ className, containerClassName, ...props }: ComponentProps<"table"> & { containerClassName?: string }) {
  return (
    <div className={cn("relative w-full overflow-x-auto", containerClassName)}>
      <table className={cn("w-full caption-bottom border-collapse text-sm", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: ComponentProps<"thead">) {
  return <thead className={cn("bg-muted", className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableFooter({ className, ...props }: ComponentProps<"tfoot">) {
  return <tfoot className={cn("border-t border-border", className)} {...props} />;
}

export function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return <tr className={cn("border-b border-border transition-colors duration-150", className)} {...props} />;
}

export function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "h-11 px-4 text-left align-middle text-[13px] font-semibold whitespace-nowrap text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("h-[68px] px-4 py-3 align-middle", className)} {...props} />;
}

export function TableCaption({ className, ...props }: ComponentProps<"caption">) {
  return <caption className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />;
}
