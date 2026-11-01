// Upload rules shared by the browser (instant feedback) and the service layer (the real check).

export type ImageRule = {
  maxBytes: number;
  /** MIME types, e.g. "image/png". */
  types: string[];
  label: string;
};

export const AVATAR_RULE: ImageRule = {
  maxBytes: 5 * 1024 * 1024,
  types: ["image/png", "image/jpeg"],
  label: "PNG, JPG up to 5 MB",
};

export const LOGO_RULE: ImageRule = {
  maxBytes: 2 * 1024 * 1024,
  types: ["image/png", "image/jpeg", "image/svg+xml"],
  label: "PNG, JPG or SVG up to 2 MB",
};

/** Product photos (Figma "Add product"): shown landscape 4:3 on the website. */
export const PRODUCT_IMAGE_RULE: ImageRule = {
  maxBytes: 5 * 1024 * 1024,
  types: ["image/png", "image/jpeg"],
  label: "PNG or JPG up to 5 MB",
};

/** Error text for a bad file, or `null` when it is fine. */
export function checkImage(file: File, rule: ImageRule): string | null {
  if (!rule.types.includes(file.type)) return `Unsupported file. Use ${rule.label}.`;
  if (file.size > rule.maxBytes) return `That file is too large. Use ${rule.label}.`;
  return null;
}

/** Photos the owner places on their website (hero, products, about). */
export const SITE_IMAGE_RULE: ImageRule = {
  maxBytes: 5 * 1024 * 1024,
  types: ["image/png", "image/jpeg", "image/webp"],
  label: "PNG, JPG or WebP up to 5 MB",
};
