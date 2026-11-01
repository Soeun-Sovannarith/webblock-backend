"use client";

import { useState, type ComponentType } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ArrowRight2, Box, Shop } from "iconsax-reactjs";
import type { NavIcon, NavItem } from "@/config/nav";
import { cn } from "@/lib/utils";

const ICONS = {
  products: Box,
  business: Shop,
  monitor: Activity,
} satisfies Record<NavIcon, ComponentType<{ className?: string }>>;

function isActive(pathname: string, item: NavItem) {
  if (pathname === item.href) return true;
  return !item.exact && pathname.startsWith(`${item.href}/`);
}

const itemClass = (active: boolean, compact = false, collapsed = false) =>
  cn(
    "flex w-full items-center gap-3 rounded-full px-3 transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
    compact ? "h-9 text-[13px]" : "h-[42px] text-sm",
    collapsed && "justify-center px-0",
    active
      ? "bg-brand-soft font-semibold text-brand-deep"
      : "font-medium text-foreground-soft hover:bg-muted hover:text-foreground",
  );

/**
 * Sidebar links. `collapsed` shows icons only (labels stay for screen readers); `compact` is the
 * denser admin size. Items with `children` become an expandable group.
 */
export function SidebarNav({
  items,
  collapsed = false,
  compact = false,
}: {
  items: NavItem[];
  collapsed?: boolean;
  compact?: boolean;
}) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className={cn("flex flex-col", compact ? "gap-1" : "gap-1.5")}>
      {items.map((item) =>
        item.children ? (
          <NavGroup key={item.href} item={item} pathname={pathname} compact={compact} collapsed={collapsed} />
        ) : (
          <NavLink
            key={item.href}
            item={item}
            active={isActive(pathname, item)}
            compact={compact}
            collapsed={collapsed}
          />
        ),
      )}
    </nav>
  );
}

function NavIconGlyph({ item, compact }: { item: NavItem; compact: boolean }) {
  const Icon = item.icon ? ICONS[item.icon] : null;
  return Icon ? <Icon aria-hidden className={cn("shrink-0", compact ? "size-4" : "size-[18px]")} /> : null;
}

function NavLink({
  item,
  active,
  compact = false,
  collapsed = false,
}: {
  item: NavItem;
  active: boolean;
  compact?: boolean;
  collapsed?: boolean;
}) {
  return (
    <Link
      href={item.href}
      title={collapsed ? item.label : undefined}
      aria-current={active ? "page" : undefined}
      className={itemClass(active, compact, collapsed)}
    >
      <NavIconGlyph item={item} compact={compact} />
      <span className={cn(collapsed && "sr-only")}>{item.label}</span>
    </Link>
  );
}

/** Item with sub-pages: a toggle button, open while one of its pages is showing. */
function NavGroup({
  item,
  pathname,
  compact = false,
  collapsed = false,
}: {
  item: NavItem;
  pathname: string;
  compact?: boolean;
  collapsed?: boolean;
}) {
  const inside = isActive(pathname, item);
  const [open, setOpen] = useState(inside);
  const listId = `nav-group-${item.href.replace(/\W+/g, "-")}`;
  const expanded = open || inside;

  return (
    <div>
      <button
        type="button"
        title={collapsed ? item.label : undefined}
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className={cn(itemClass(false, compact, collapsed), inside && "text-foreground")}
      >
        <NavIconGlyph item={item} compact={compact} />
        <span className={cn("flex-1 text-left", collapsed && "sr-only")}>{item.label}</span>
        {!collapsed && (
          <ArrowRight2 aria-hidden className={cn("size-4 transition-transform duration-150", expanded && "rotate-90")} />
        )}
      </button>
      {/* Slides open/closed (grid rows 0fr ⇄ 1fr) instead of popping; `inert` keeps hidden links out of Tab. */}
      <div
        inert={!expanded}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
          expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
      >
        {/* Padding offset by negative margin: room for the links' focus ring inside the clip. */}
        <div className="-mx-1 -mb-1 overflow-hidden px-1 pb-1">
          <div
            id={listId}
            className={cn("mt-1 flex flex-col gap-1", collapsed ? "" : "ml-[21px] border-l border-border pl-3")}
          >
            {item.children?.map((child) => (
              <NavLink
                key={child.href}
                item={child}
                active={isActive(pathname, child)}
                compact={compact}
                collapsed={collapsed}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
