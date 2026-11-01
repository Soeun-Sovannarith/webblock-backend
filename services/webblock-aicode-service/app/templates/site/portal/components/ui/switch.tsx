"use client";

import { cn } from "@/portal/lib/utils";

type SwitchProps = {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** Required: switches have no visible text of their own. */
  "aria-label": string;
  disabled?: boolean;
};

export function Switch({ checked, onCheckedChange, disabled, ...props }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-[22px] w-[38px] shrink-0 cursor-pointer items-center rounded-full transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/40 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-brand" : "bg-violet-border",
      )}
      {...props}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-4 rounded-full bg-background shadow-button transition-transform",
          checked ? "translate-x-[19px]" : "translate-x-[3px]",
        )}
      />
    </button>
  );
}
