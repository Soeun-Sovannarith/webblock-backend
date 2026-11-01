"use client";

import { createContext, use } from "react";

/**
 * Where floating layers (Select, DropdownMenu, Popover) portal to. Defaults to <body>.
 * A native modal <dialog> lives in the browser's top layer, above anything in <body> whatever its
 * z-index, so Modal provides its <dialog> here and the menus open inside it instead.
 */
export const PortalContainerContext = createContext<HTMLElement | null>(null);

export function usePortalContainer(): HTMLElement | undefined {
  return use(PortalContainerContext) ?? undefined;
}
