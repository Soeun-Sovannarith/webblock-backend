import Image from "next/image";
import { Gallery } from "iconsax-reactjs";
import { cn } from "@/lib/utils";

/** Product photo, always 4:3. Without a photo it shows a soft placeholder with a gallery icon. */
export function ProductImage({
  src,
  alt,
  sizes = "320px",
  className,
}: {
  src: string | null;
  alt: string;
  /** `sizes` hint for the browser, e.g. "64px" for table thumbnails. */
  sizes?: string;
  /** Width and corner radius, e.g. "w-16 rounded-lg". */
  className?: string;
}) {
  return (
    <span className={cn("relative block aspect-[4/3] shrink-0 overflow-hidden rounded-[10px] bg-preview-panel", className)}>
      {src ? (
        <Image src={src} alt={alt} fill unoptimized sizes={sizes} className="object-cover" />
      ) : (
        <span role="img" aria-label={alt} className="absolute inset-0 flex items-center justify-center text-placeholder">
          <Gallery aria-hidden className="size-[30%] max-h-8 max-w-8" />
        </span>
      )}
    </span>
  );
}
