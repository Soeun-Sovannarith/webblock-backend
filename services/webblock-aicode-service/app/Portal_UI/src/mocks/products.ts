import type { Product } from "@/features/products/types";
import { PETAL_AND_STEM_ID } from "./session";
import { daysAgo, hoursAgo } from "./time";

const photo = (file: string) => `/mock/template-sites/everyday-store/${file}`;

/** Product categories each website shows as filter tabs and in the product form. */
export const productCategories: Record<string, string[]> = {
  [PETAL_AND_STEM_ID]: ["Flowers", "Bouquets", "Gifts", "Events"],
};

type Seed = Pick<Product, "name" | "description" | "category" | "price" | "imageUrl">;

const petalAndStem: Seed[] = [
  {
    name: "Blue lily stem",
    category: "Flowers",
    price: "6.00",
    imageUrl: photo("hero.png"),
    description: "A single long-stemmed blue lily, cut fresh each morning. Looks lovely on its own in a slim vase.",
  },
  {
    name: "Gift bouquet",
    category: "Bouquets",
    price: "38.00",
    imageUrl: photo("candle.png"),
    description: "A hand-tied bouquet of seasonal flowers, wrapped in kraft paper with a ribbon and a small card.",
  },
  {
    name: "Lily bunch",
    category: "Flowers",
    price: "16.00",
    imageUrl: photo("blanket.png"),
    description: "Three lily stems tied by hand. The buds open over a few days and fill the room with a soft scent.",
  },
  {
    name: "Stone vase",
    category: "Gifts",
    price: "24.00",
    imageUrl: photo("stone-vase.png"),
    description: "A handmade ceramic vase with a matte stone finish. Holds a small bouquet or stands well on its own.",
  },
  {
    name: "Table arrangement",
    category: "Events",
    price: "45.00",
    imageUrl: photo("mirror.png"),
    description:
      "A low arrangement of seasonal flowers and greenery for dining tables and events. Order two days ahead.",
  },
  {
    name: "Spring tulip bunch",
    category: "Flowers",
    price: "14.00",
    imageUrl: photo("white-vase.png"),
    description: "This week’s special: ten fresh tulips in soft spring colours, tied with natural twine.",
  },
  {
    name: "Peony bouquet",
    category: "Bouquets",
    price: "42.00",
    imageUrl: photo("candle.png"),
    description: "Full pink peonies with eucalyptus, wrapped in soft paper. Available while peonies are in season.",
  },
  {
    name: "Ceramic bud vase",
    category: "Gifts",
    price: "12.00",
    imageUrl: photo("white-vase.png"),
    description: "A small glazed vase for one or two stems. Fits on a desk, a shelf or a bedside table.",
  },
  {
    name: "Dried grass bunch",
    category: "Flowers",
    price: "18.00",
    imageUrl: photo("hero.png"),
    description: "Natural dried grasses that keep their shape for months. No water needed.",
  },
  {
    name: "Petal candle",
    category: "Gifts",
    price: "15.00",
    imageUrl: null,
    description: "A soy wax candle topped with dried petals. Burns for about 30 hours with a light floral scent.",
  },
  {
    name: "Pampas stem",
    category: "Flowers",
    price: "9.00",
    imageUrl: photo("stone-vase.png"),
    description: "One tall, fluffy pampas stem in natural beige. Pairs well with the stone vase.",
  },
  {
    name: "Wedding and event centrepiece set with matching aisle flowers and a welcome sign garland",
    category: "Events",
    price: "120.00",
    imageUrl: photo("mirror.png"),
    description:
      "Five matching centrepieces, aisle flowers and a garland for your welcome sign, designed with you and set up on the day. Talk to us at least three weeks before your event.",
  },
];

let n = 0;
function toProducts(websiteId: string, seeds: Seed[]): Product[] {
  return seeds.map((seed, i) => {
    n += 1;
    return {
      ...seed,
      id: `5e1c0a7b-3f2d-4c6e-9a8b-${String(n).padStart(12, "0")}`,
      websiteId,
      // Newest first in the list: the first seed was added most recently.
      createdAt: daysAgo(2 + i * 3),
      updatedAt: i === 0 ? hoursAgo(5) : daysAgo(1 + i * 3),
    };
  });
}

export const products: Product[] = [
  ...toProducts(PETAL_AND_STEM_ID, petalAndStem),
];
