/**
 * Business-Unit-scoped access-management fixtures (Roles & Access screen) —
 * plain data + functions, server-safe (imported by the app/api/onboarding
 * route AND by mocks/handlers.ts, so a person onboarded from Users or from
 * Roles & Access shows up in both places). This is the DUMMY-DATA source;
 * a real backend RBAC service replaces the route-handler bodies, not these
 * shapes.
 */

export type MockMember = {
  userId: string;
  name: string | null;
  email: string | null;
  initials: string;
  roles: string[];
};

export const ACCESS_WORKSPACES = [
  { id: "ws_payments", name: "Payments" },
  { id: "ws_lending", name: "Lending" },
  { id: "ws_platform", name: "Platform Engineering" },
];

// Sarthak is governance tier, so he holds no delivery role anywhere (§14.6).
// Diego is Developer in Payments and Architect in Lending — a role is a
// binding of (person, scope, role), not a property of the person (§33.1).
export const ACCESS_MEMBERS: Record<string, MockMember[]> = {
  ws_payments: [
    { userId: "auth0|admin", name: "Sarthak Kapoor", email: "srk02804@gmail.com", initials: "SK", roles: ["org_admin"] },
    { userId: "auth0|pm01", name: "Priya Menon", email: "priya@abcbank.com", initials: "PM", roles: ["ba"] },
    { userId: "auth0|tl01", name: "Diego Alvarez", email: "diego@abcbank.com", initials: "DA", roles: ["developer"] },
    { userId: "auth0|qa01", name: "Wei Chen", email: "wei@abcbank.com", initials: "WC", roles: ["qa"] },
  ],
  ws_lending: [
    { userId: "auth0|admin", name: "Sarthak Kapoor", email: "srk02804@gmail.com", initials: "SK", roles: ["org_admin"] },
    { userId: "auth0|tl01", name: "Diego Alvarez", email: "diego@abcbank.com", initials: "DA", roles: ["architect"] },
    { userId: "auth0|sre01", name: "Lena Fischer", email: "lena@abcbank.com", initials: "LF", roles: ["devops_engineer"] },
  ],
  ws_platform: [
    { userId: "auth0|admin", name: "Sarthak Kapoor", email: "srk02804@gmail.com", initials: "SK", roles: ["org_admin"] },
    { userId: "auth0|sre01", name: "Lena Fischer", email: "lena@abcbank.com", initials: "LF", roles: ["devops_engineer"] },
  ],
};

// All org users (full roster, no workspace context)
export const ORG_MEMBERS = [
  { userId: "auth0|admin", email: "srk02804@gmail.com", initials: "SK" },
  { userId: "auth0|pm01", email: "priya@acme.test", initials: "PM" },
  { userId: "auth0|tl01", email: "diego@acme.test", initials: "DA" },
  { userId: "auth0|qa01", email: "wei@acme.test", initials: "WC" },
  { userId: "auth0|sre01", email: "lena@acme.test", initials: "LF" },
];

export function mockInitials(handle: string, name?: string | null): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
  }
  return (handle.replace(/^.*\|/, "").slice(0, 2) || "?").toUpperCase();
}

/**
 * Add (or grant an additional role to) a person in a Business Unit's access
 * list — the write side `POST /api/onboarding` calls so a person onboarded
 * from Users or Roles & Access shows up in this list too, not just in
 * `lib/mock/workspace-fixtures.ts`'s membership store.
 */
export function addAccessMember(
  workspaceId: string,
  person: { userId: string; name?: string | null; email?: string | null },
  roleName: string,
): MockMember {
  const list = (ACCESS_MEMBERS[workspaceId] ??= []);
  let member = list.find((m) => m.userId === person.userId);
  if (!member) {
    member = {
      userId: person.userId,
      name: person.name ?? null,
      email: person.email ?? null,
      initials: mockInitials(person.userId, person.name),
      roles: [],
    };
    list.push(member);
  }
  if (!member.roles.includes(roleName)) member.roles.push(roleName);
  return member;
}
