// Owned by the WebBlock harness and refreshed on every run. A product photo uploaded in the owner portal, stored in
// object storage (lib/storage.ts) and listed in catalog.product_images (lib/products.ts). Public, like the shop that
// shows it.
import { productPhoto } from "@/lib/products";
import { fileResponse } from "@/lib/media";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    return fileResponse(await productPhoto(id));
  } catch (error) {
    console.error("[webblock] Could not load a product photo:", (error as { code?: string }).code ?? "error");
    return new Response(null, { status: 503 });
  }
}
