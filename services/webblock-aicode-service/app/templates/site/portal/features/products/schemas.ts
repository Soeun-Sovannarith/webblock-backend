import { z } from "zod";

export const productInput = z.object({
  name: z.string().trim().min(2, "Enter a product name.").max(120, "Use 120 characters or fewer."),
  category: z.string().trim().min(1, "Choose a category."),
  description: z
    .string()
    .trim()
    .min(10, "Describe the product in at least 10 characters.")
    .max(300, "Use 300 characters or fewer."),
  price: z
    .string()
    .trim()
    .regex(/^\d{1,6}(\.\d{1,2})?$/, "Enter a price like 12 or 12.50.")
    // Stored as a decimal string with two places, like the API returns it.
    .transform((value) => Number(value).toFixed(2)),
});

export type ProductInput = z.infer<typeof productInput>;
