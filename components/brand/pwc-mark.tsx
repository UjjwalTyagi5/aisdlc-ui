import Image from "next/image";

import { cn } from "@/lib/utils";

/**
 * PwC brand mark — the official logo on a self-contained white plate so the
 * full-colour wordmark reads correctly on ANY surface (light or dark theme).
 * The plate is intentionally fixed white: third-party brand logos must keep
 * their own colours rather than inherit foreground/invert.
 *
 * `size` is the plate edge length in px. The logo is inset within it.
 */
export interface PwcMarkProps {
  size?: number;
  className?: string;
  /** Render the bare logo with no plate (caller guarantees a light backdrop). */
  bare?: boolean;
}

export function PwcMark({ size = 36, className, bare }: PwcMarkProps) {
  const logo = (
    <Image
      src="/brand/pwc-logo.png"
      alt="PwC"
      width={Math.round(size * (bare ? 1 : 0.72))}
      height={Math.round(size * (bare ? 1 : 0.72))}
      priority
      className="object-contain"
    />
  );

  if (bare) return logo;

  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center rounded-xl bg-white shadow-sm ring-1 ring-black/[0.06]",
        className,
      )}
      style={{ width: size, height: size }}
    >
      {logo}
    </span>
  );
}
