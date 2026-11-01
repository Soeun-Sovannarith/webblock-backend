"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useUrlParam } from "@/hooks/use-url-param";
import { cn } from "@/lib/utils";

/** "Rows [10]" box: how many rows the table shows, stored in the URL as `?size=`. */
export function RowsSelect({
  value,
  options = [10, 20, 50],
  defaultSize = 10,
}: {
  value: number;
  options?: number[];
  /** Size the page uses without `?size=`; picking it removes the param. */
  defaultSize?: number;
}) {
  const { setParam, pending } = useUrlParam();

  return (
    <div className={cn("flex items-center gap-2", pending && "opacity-70")}>
      <span className="text-[13px] text-placeholder">Rows</span>
      <Select
        value={String(value)}
        onValueChange={(next) => setParam("size", next === String(defaultSize) ? null : next)}
      >
        <SelectTrigger
          aria-label="Rows per page"
          className="h-[34px] w-[70px] cursor-pointer gap-1 rounded-full border border-border bg-background pr-2.5 pl-3.5 text-sm font-medium text-foreground transition-[border-color,box-shadow] focus-visible:border-primary focus-visible:ring-3 focus-visible:ring-ring/25 data-[state=open]:border-primary [&_svg]:size-3.5"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="end" className="min-w-[96px]">
          {options.map((option) => (
            <SelectItem key={option} value={String(option)}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
