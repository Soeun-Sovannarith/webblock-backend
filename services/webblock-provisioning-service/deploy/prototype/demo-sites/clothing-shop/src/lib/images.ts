// Maps a catalog image's `storage_key` to a URL this site serves.
//
// The design requires every `storage_key` to start with the owning website's ID
// (`CHECK (storage_key LIKE website_id::text || '/%')`). The shop's pictures ship inside the image under
// `public/products/`, so a key `<website id>/products/linen-shirt.svg` is served as
// `/products/linen-shirt.svg`. Only that one shape is accepted: anything else (another folder, a path
// with "..", another file type) gets no URL and the page shows a neutral placeholder instead.
const STORAGE_KEY_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(products\/[a-z0-9][a-z0-9-]{0,99}\.svg)$/;

/** Returns the public URL of a catalog image, or null when the key is not a bundled product image. */
export function imageSrc(storageKey: string): string | null {
  const match = STORAGE_KEY_PATTERN.exec(storageKey);
  return match?.[1] === undefined ? null : `/${match[1]}`;
}
