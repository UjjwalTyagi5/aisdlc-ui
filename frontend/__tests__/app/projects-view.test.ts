/**
 * Which projects view a viewer gets, from the URL and their standing.
 *
 * The table is an admin view: an Organization or Business Unit Admin scans a
 * portfolio for the two projects that are stuck, where a card gives each one room and
 * puts the same field in a different place on every tile. Everyone else works on a
 * handful of projects they know by name, and keeps grid and list unchanged.
 *
 * The case worth pinning is the bookmark. An admin sends a contributor a link to
 * `?view=table`; the contributor has no Table button, so rendering one would leave
 * them in a view with no toggle to leave it by. Casting the param straight to the
 * union — which is what every other filter on that page does — would do exactly that.
 */
import { describe, expect, it } from "vitest";

import { resolveProjectsView } from "@/components/app/projects-toolbar";

const ADMIN = true;
const NOT_ADMIN = false;

describe("resolveProjectsView", () => {
  it("defaults an admin to the table and everyone else to the grid", () => {
    expect(resolveProjectsView(null, ADMIN)).toBe("table");
    expect(resolveProjectsView(null, NOT_ADMIN)).toBe("grid");
  });

  it("honours grid and list for everybody — those are unchanged", () => {
    for (const canUseTable of [ADMIN, NOT_ADMIN]) {
      expect(resolveProjectsView("grid", canUseTable)).toBe("grid");
      expect(resolveProjectsView("list", canUseTable)).toBe("list");
    }
  });

  it("gives a non-admin their default for ?view=table, not a table they cannot leave", () => {
    expect(resolveProjectsView("table", NOT_ADMIN)).toBe("grid");
    expect(resolveProjectsView("table", ADMIN)).toBe("table");
  });

  it("falls back to the viewer's default on anything unrecognised", () => {
    // A hand-edited URL, or a value from a build that offered a view this one does not.
    for (const junk of ["", "TABLE", "kanban", "1", "null"]) {
      expect(resolveProjectsView(junk, ADMIN)).toBe("table");
      expect(resolveProjectsView(junk, NOT_ADMIN)).toBe("grid");
    }
  });

  it("never returns a view the viewer has no toggle for", () => {
    // The invariant behind all of the above, stated once: whatever arrives, a viewer
    // without the Table button never lands on the table.
    for (const requested of [null, "", "table", "grid", "list", "nonsense"]) {
      expect(resolveProjectsView(requested, NOT_ADMIN)).not.toBe("table");
    }
  });
});
