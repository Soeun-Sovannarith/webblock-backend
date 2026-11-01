// Sidebar items. No count badges.

/** Icon names, mapped to iconsax icons in components/layout/sidebar-nav.tsx. */
export type NavIcon = "products" | "business" | "monitor";

export type NavItem = {
  label: string;
  href: string;
  /** Only active on this exact path (for section roots). */
  exact?: boolean;
  icon?: NavIcon;
  /** Sub-pages shown under the item; the item itself is then a group toggle, not a link. */
  children?: NavItem[];
};

export function ownerNav(): NavItem[] {
  const base = "/portal";
  return [
    { label: "Products", href: `${base}/products`, icon: "products" },
    { label: "Business information", href: `${base}/info`, icon: "business" },
    { label: "Monitor", href: `${base}/monitoring`, icon: "monitor" },
  ];
}
