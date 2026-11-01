import type { ReactNode } from "react";
import Image from "next/image";
import { Global, Shop } from "iconsax-reactjs";
import { formatClock, initials } from "@/lib/format";
import { cn } from "@/lib/utils";
import { WEEKDAYS } from "@/features/websites/schemas";
import type { OpeningHour } from "@/features/websites/types";
import type { BusinessInfoView } from "../service";

// Pieces shared by the read view (Server Component) and the edit form (client).

export const DAY_LABEL = Object.fromEntries(WEEKDAYS.map((w) => [w.day, w.label])) as Record<OpeningHour["day"], string>;

export function InfoCard({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cn("rounded-[20px] border border-border bg-background p-5 shadow-surface", className)}>
      {children}
    </section>
  );
}

export function InfoCardTitle({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="pb-3">
      <h2 className="font-sans text-[17px] leading-6 font-semibold text-foreground">{children}</h2>
      {hint && <p className="pt-1 text-xs leading-[18px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Small uppercase heading inside a card: ABOUT, CONTACT, LOCATION. */
export function SectionLabel({ children }: { children: ReactNode }) {
  return <h3 className="pt-3 pb-3 font-sans text-[11px] leading-4 font-medium tracking-wide text-muted-foreground uppercase">{children}</h3>;
}

/** Top of the website as visitors see it, cropped to the hero. */
export function SiteBanner({ website }: { website: BusinessInfoView["website"] }) {
  return (
    <div className="relative h-[220px] overflow-hidden rounded-xl bg-preview-panel sm:h-[300px]">
      <Image
        src={website.thumbnailUrl}
        alt={`Top of the ${website.name} website`}
        fill
        unoptimized
        sizes="(min-width: 1024px) 75vw, 100vw"
        className="object-cover object-top"
      />
    </div>
  );
}

/** Round logo, or the business initials when there is none. */
export function BusinessMark({ name, logoUrl, className }: { name: string; logoUrl: string | null; className?: string }) {
  return (
    <span
      className={cn(
        "relative flex size-[52px] shrink-0 items-center justify-center overflow-hidden rounded-full bg-brand-soft font-heading text-lg font-semibold text-brand-strong",
        className,
      )}
    >
      {logoUrl ? (
        <Image src={logoUrl} alt={`${name} logo`} fill unoptimized sizes="52px" className="object-cover" />
      ) : (
        <span aria-hidden>{initials(name)}</span>
      )}
    </span>
  );
}

/** Business name with web address and business type under it. */
export function BusinessIdentity({ website, children }: { website: BusinessInfoView["website"]; children?: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="font-heading text-xl leading-7 font-semibold text-foreground">{website.name}</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-0.5 text-[13px] leading-5 text-muted-foreground">
        <span className="inline-flex items-center gap-1.5 text-foreground-soft">
          <Global aria-hidden className="size-3.5" />
          {website.domain ?? "Not published yet"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Shop aria-hidden className="size-3.5" />
          Business type: <span className="text-foreground-soft">{website.businessType}</span>
        </span>
      </div>
      {children}
    </div>
  );
}

/** "9:00 AM – 6:00 PM", or `null` when the business is closed that day. */
export function hoursText(hour: OpeningHour): string | null {
  return hour.open ? `${formatClock(hour.opensAt)} – ${formatClock(hour.closesAt)}` : null;
}
