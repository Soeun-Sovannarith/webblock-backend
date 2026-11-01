export type Product = {
  id: string;
  websiteId: string;
  name: string;
  description: string;
  /** One of the website's product categories, e.g. "Flowers". */
  category: string;
  /** Decimal string in USD, e.g. "6.00". */
  price: string;
  /** 4:3 photo, or `null` when the owner has not added one. */
  imageUrl: string | null;
  createdAt: string;
  updatedAt: string;
};

/** Products per category for the filter tabs. `total` counts every product of the website. */
export type ProductCategoryCount = { category: string; count: number };

export type ProductCounts = { total: number; categories: ProductCategoryCount[] };
