import type { ComponentProps } from "react";
import { cn } from "@/portal/lib/utils";

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-28 w-full resize-y rounded-2xl border border-input bg-background px-4 py-3 text-[15px] text-foreground placeholder:text-placeholder outline-none transition-[border-color,box-shadow] focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-ring/25 aria-invalid:border-danger aria-invalid:ring-danger/20",
        className,
      )}
      {...props}
    />
  );
}
