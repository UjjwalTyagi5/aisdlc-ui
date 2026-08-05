import { redirect } from "next/navigation";

/**
 * Assigning a role now happens on Users, next to the person it applies to —
 * this screen made you choose a scope first and then hunt someone inside it,
 * and answered "what does this person hold across the organisation" nowhere.
 *
 * Roles & Access keeps what it is for: what a role MEANS. Redirect rather than
 * delete, so sidebar links, bookmarks and the command palette keep working.
 */
export default function AccessAssignmentsPage() {
  redirect("/admin/access/roles");
}
