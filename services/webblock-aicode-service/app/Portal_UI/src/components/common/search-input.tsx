"use client";

import { useEffect, useState } from "react";
import { SearchNormal1 } from "iconsax-reactjs";
import { useUrlParam } from "@/hooks/use-url-param";
import { cn } from "@/lib/utils";

type SearchInputProps = {
  /** Value currently applied in the URL (from the page's searchParams). */
  defaultValue: string;
  placeholder: string;
  /** Accessible name. */
  label: string;
  /** `pill` is the compact toolbar search, `hero` the big one with a round button. */
  variant?: "pill" | "hero";
  className?: string;
};

/** Search box that keeps its text in the URL (`?q=`), so results are shareable and survive refresh. */
export function SearchInput({
  defaultValue,
  placeholder,
  label,
  variant = "pill",
  className,
}: SearchInputProps) {
  const { setParam, pending } = useUrlParam();
  const [value, setValue] = useState(defaultValue);

  useEffect(() => {
    const next = value.trim();
    if (next === defaultValue) return;
    const timer = setTimeout(() => setParam("q", next), 300);
    return () => clearTimeout(timer);
  }, [value, defaultValue, setParam]);

  const hero = variant === "hero";

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        setParam("q", value.trim());
      }}
      className={cn(
        "relative flex items-center rounded-full border border-border bg-background transition-[border-color,box-shadow] focus-within:border-primary focus-within:ring-3 focus-within:ring-ring/25",
        hero
          ? "h-14 w-full max-w-[560px] gap-2.5 pr-1.5 pl-[22px] shadow-[0_1px_1px_rgb(60_120_207/0.04),0_12px_14px_rgb(60_120_207/0.12)]"
          : "h-11 w-[360px] max-w-full gap-2.5 px-[14px]",
        pending && "opacity-80",
        className,
      )}
    >
      <SearchNormal1
        aria-hidden
        className="size-[17px] shrink-0 text-placeholder"
      />
      <input
        type="search"
        value={value}
        maxLength={100}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className={cn(
          "h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-placeholder [&::-webkit-search-cancel-button]:appearance-none",
        )}
      />
      {hero && (
        <button
          type="submit"
          aria-label="Search"
          className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-[0_6px_14px_rgb(47_107_196/0.35)] transition-colors outline-none hover:bg-brand focus-visible:ring-3 focus-visible:ring-ring/40 focus-visible:ring-offset-2"
        >
          <SearchNormal1 aria-hidden className="size-[18px]" />
        </button>
      )}
    </form>
  );
}
