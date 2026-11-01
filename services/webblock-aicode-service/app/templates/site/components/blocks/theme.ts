// Owned by the WebBlock harness and refreshed on every run. Turns the Puck style props (root: theme, accent, font;
// every block: tone, align, spacing) into the attributes that theme.css styles. Shared with the Puck editor.
import type { CSSProperties } from "react";

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

const choice = (value: unknown) => (typeof value === "string" && value && value !== "default" ? value : undefined);

export function siteAttributes(props: Record<string, unknown> = {}) {
  const accent = typeof props.accent === "string" && HEX_COLOR.test(props.accent) ? props.accent : undefined;
  return {
    className: "wb-site",
    "data-theme": choice(props.theme),
    "data-font": choice(props.font),
    style: accent ? ({ "--wb-accent": accent } as CSSProperties) : undefined,
  };
}

export function blockAttributes(props: Record<string, unknown> = {}) {
  return {
    className: "wb-block",
    "data-tone": choice(props.tone),
    "data-align": choice(props.align),
    "data-spacing": choice(props.spacing),
  };
}
