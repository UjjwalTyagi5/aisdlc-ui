import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * `<Icon />` — the canonical wrapper for local SVG icons (brand marks,
 * connector logos, agent glyphs) that don't live in `lucide-react`.
 *
 * Usage (preferred — import the SVG as a component via SVGR/Next):
 *   import LogoMark from "@/components/icons/logo.svg";
 *   <Icon as={LogoMark} size={20} />
 *
 * Or render raw children:
 *   <Icon size={24} aria-hidden><path d="…" /></Icon>
 *
 * Size is always `currentColor`-aware — callers control color via `text-*`.
 */
export interface IconProps extends React.SVGAttributes<SVGSVGElement> {
  size?: number | string;
  as?: React.ComponentType<React.SVGAttributes<SVGSVGElement>>;
}

export const Icon = React.forwardRef<SVGSVGElement, IconProps>(
  ({ as: Component, size = 16, className, children, ...props }, ref) => {
    const commonProps: React.SVGAttributes<SVGSVGElement> = {
      width: size,
      height: size,
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
      viewBox: "0 0 24 24",
      focusable: false,
      className: cn("inline-block align-[-0.125em]", className),
      ...props,
    };

    if (Component) {
      // Consumers of `as` must accept SVG attributes; ref is intentionally
      // not forwarded here — custom components should manage their own refs.
      return <Component {...commonProps} />;
    }
    return (
      <svg ref={ref} {...commonProps}>
        {children}
      </svg>
    );
  },
);
Icon.displayName = "Icon";
