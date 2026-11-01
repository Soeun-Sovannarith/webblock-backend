import { describe, expect, it } from "vitest";

import { imageSrc } from "@/lib/images";

const WEBSITE = "4f9c2b1e-8d3a-4c5b-9e7f-0a1b2c3d4e5f";

describe("imageSrc", () => {
  it("serves a website's product picture from the bundled public folder", () => {
    expect(imageSrc(`${WEBSITE}/products/crew-tee-white.svg`)).toBe("/products/crew-tee-white.svg");
  });

  it("gives no URL for keys outside the bundled product pictures", () => {
    for (const key of [
      "products/crew-tee-white.svg",
      `${WEBSITE}/products/../../etc/passwd`,
      `${WEBSITE}/products/a/b.svg`,
      `${WEBSITE}/logos/logo.svg`,
      `${WEBSITE}/products/photo.jpg`,
      `${WEBSITE}/products/.svg`,
      `https://example.com/${WEBSITE}/products/x.svg`,
      `not-a-uuid/products/x.svg`,
      "",
    ]) {
      expect(imageSrc(key), key).toBeNull();
    }
  });
});
