// Server-side file helpers for the mock backend: keep an uploaded image as a data URL.
// With Spring, the file goes to storage and the API returns a URL instead.

export async function imageToDataUrl(file: File): Promise<string> {
  const base64 = Buffer.from(await file.arrayBuffer()).toString("base64");
  return `data:${file.type};base64,${base64}`;
}

/** A real, non-empty file from a form field, or `null`. */
export function formFile(formData: FormData, name: string): File | null {
  const value = formData.get(name);
  return value instanceof File && value.size > 0 ? value : null;
}
