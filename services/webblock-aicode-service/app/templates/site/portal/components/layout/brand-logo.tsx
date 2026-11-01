import { siteConfig } from "@/portal/config/site";
import { cn } from "@/portal/lib/utils";

/** WebBlock mark (2×2 blocks on a blue gradient) with the wordmark. `compact` shows the mark only. */
export function BrandLogo({
  href,
  compact,
  className,
}: {
  href: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    // A plain link: leaving the portal loads the page afresh, so the portal's stylesheet stays on /portal.
    <a
      href={href}
      aria-label={siteConfig.name}
      className={cn(
        "flex items-center gap-2.5 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
        className,
      )}
    >
      <span
        aria-hidden
        className="grid size-8 shrink-0 grid-cols-2 gap-[3px] rounded-[9px] bg-linear-[145deg] from-logo-from from-6% to-logo-to to-94% p-[7px] shadow-[0_4px_5px_rgb(67_121_214/0.6),inset_0_1px_0_rgb(255_255_255/0.25)]"
      >
        <span className="rounded-[2.5px] bg-background" />
        <span className="rounded-[2.5px] bg-background opacity-65" />
        <span className="rounded-[2.5px] bg-background opacity-80" />
        <span className="rounded-[2.5px] bg-background" />
      </span>
      {!compact && (
        <span className="font-heading text-xl leading-[30px] font-bold tracking-[-0.4px] text-heading">
          {siteConfig.name}
        </span>
      )}
    </a>
  );
}
