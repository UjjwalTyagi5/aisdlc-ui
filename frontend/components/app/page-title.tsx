import * as React from "react";

/**
 * The page's name, for assistive tech only.
 *
 * The visible name lives in the breadcrumb, in the chrome — printing it again
 * as a 38px heading plus a paragraph of explanation cost roughly 200px above
 * every list, pushing the actual content below the fold on a laptop. The
 * explanation had a second problem: it described the concept, which is useful
 * exactly once, and then sat there forever for the people who read it daily.
 *
 * What could NOT go is the heading itself. A page with no `h1` gives a screen
 * reader nothing to announce on arrival and nothing to jump to — the breadcrumb
 * trail is a navigation landmark, not a heading, so it does not stand in. This
 * keeps one `h1` per page and hides it visually.
 *
 * Use on pages whose name the breadcrumb already shows. A record page whose
 * title IS data — a business unit, a provider — keeps its visible heading:
 * there the name is the content, not a label for it.
 */
export function PageTitle({ children }: { children: React.ReactNode }) {
  return <h1 className="sr-only">{children}</h1>;
}
