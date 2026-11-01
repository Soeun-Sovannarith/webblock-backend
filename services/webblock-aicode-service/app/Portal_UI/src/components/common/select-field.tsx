"use client";

import { useState, type ReactNode } from "react";
import { fieldClasses } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export type SelectOption = { value: string; label: string; disabled?: boolean };

// Radix Select reserves "" for "nothing selected", but our options use "" for "All" / "None".
const EMPTY = "__empty__";
const toRadix = (value: string) => (value === "" ? EMPTY : value);
const fromRadix = (value: string) => (value === EMPTY ? "" : value);

type SelectFieldProps = {
  options: SelectOption[];
  /** Controlled value. Leave out and use `defaultValue` for a plain form field. */
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Form field name: the choice is posted like a native select. */
  name?: string;
  id?: string;
  placeholder?: string;
  /** Icon inside the field, on the left. */
  icon?: ReactNode;
  iconClassName?: string;
  /** Trigger classes (size, background…) on top of the standard field look. */
  className?: string;
  contentClassName?: string;
  disabled?: boolean;
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
};

/** Form dropdown: field-style trigger, and the WebBlock menu (same as the card "…" menu) when open. */
export function SelectField({
  options,
  value,
  defaultValue,
  onValueChange,
  name,
  id,
  placeholder,
  icon,
  iconClassName,
  className,
  contentClassName,
  disabled,
  ...aria
}: SelectFieldProps) {
  const [inner, setInner] = useState(defaultValue ?? "");
  const current = value ?? inner;
  // An empty value with a placeholder shows the placeholder, unless "" is a real option ("All").
  const showPlaceholder =
    current === "" && !options.some((o) => o.value === "" && !o.disabled);

  return (
    <>
      <Select
        value={showPlaceholder ? "" : toRadix(current)}
        onValueChange={(next) => {
          const v = fromRadix(next);
          if (value === undefined) setInner(v);
          onValueChange?.(v);
        }}
        disabled={disabled}
      >
        <SelectTrigger
          id={id}
          {...aria}
          className={cn(
            fieldClasses,
            "relative w-full cursor-pointer pr-4 data-[state=open]:border-primary",
            icon && "pl-11",
            className,
          )}
        >
          {icon && (
            <span
              aria-hidden
              className={cn(
                "pointer-events-none absolute inset-y-0 left-4 flex items-center text-foreground [&_svg]:size-[18px]",
                iconClassName,
              )}
            >
              {icon}
            </span>
          )}
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent className={contentClassName}>
          {options
            .filter((o) => !(o.disabled && o.value === ""))
            .map((o) => (
              <SelectItem
                key={o.value}
                value={toRadix(o.value)}
                disabled={o.disabled}
              >
                {o.label}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
      {name && <input type="hidden" name={name} value={current} />}
    </>
  );
}
