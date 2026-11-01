// Server-side file helpers: an uploaded image, read into memory for object storage (lib/storage.ts).
import type { Photo } from "@/lib/products";

export async function imageToPhoto(file: File): Promise<Photo> {
  return { contentType: file.type, bytes: Buffer.from(await file.arrayBuffer()) };
}

/** A real, non-empty file from a form field, or `null`. */
export function formFile(formData: FormData, name: string): File | null {
  const value = formData.get(name);
  return value instanceof File && value.size > 0 ? value : null;
}
