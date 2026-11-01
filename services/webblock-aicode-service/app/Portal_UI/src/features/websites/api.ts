// Data access for websites. Mock now, Spring later: only these function bodies change.
import { ApiError } from "@/lib/api/errors";
import { delay } from "@/lib/api/delay";
import { websites } from "@/mocks/websites";
import type { Website } from "./types";

export async function getWebsite(websiteId: string): Promise<Website> {
  await delay(100);
  const website = websites.find((w) => w.id === websiteId);
  if (!website) {
    throw new ApiError({ title: "Website not found", status: 404, detail: "That website no longer exists." });
  }
  return website;
}
