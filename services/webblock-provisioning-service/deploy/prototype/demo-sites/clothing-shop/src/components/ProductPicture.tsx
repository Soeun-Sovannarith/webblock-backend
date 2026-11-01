import Image from "next/image";

import type { ProductImage } from "@/lib/catalog";

type Props = {
  image: ProductImage | null;
  /** Used as the description when the product has no picture. */
  productName: string;
  priority?: boolean;
  sizes: string;
};

/** A product picture in a 4:5 frame, or a quiet placeholder when the product has none. */
export function ProductPicture({ image, productName, priority = false, sizes }: Props) {
  if (image === null || image.src === null) {
    return (
      <div className="picture picture--empty" role="img" aria-label={`No picture yet for ${productName}`}>
        <span aria-hidden="true">Picture coming soon</span>
      </div>
    );
  }
  return (
    <div className="picture">
      <Image src={image.src} alt={image.alt} width={800} height={1000} sizes={sizes} priority={priority} />
    </div>
  );
}
