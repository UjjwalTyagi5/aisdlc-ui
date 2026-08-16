# RBAC tables: what each one is, who owns it, and how to query it

Five tables decide what anyone can do. They divide along one line that matters more than
any other: **who is allowed to write them.** Three are owned by the code and rebuilt from
it; two are owned by your organisation and written from the UI. Confusing the two is the
mistake this document exists to prevent — an edit to a code-owned table works perfectly
until the next backend restart and then silently disappears.

```
CODE-OWNED  (reconciled from shared/authz/permissions.py on every boot)
  permissions            every permission string that can exist
  roles                  the 13 built-in roles
  role_permissions       what each built-in role SHIPS WITH  ← the default

ORG-OWNED   (written by the product, never touched by the seeder)
  role_permission_overrides   what a built-in role grants HERE, if retuned
  role_bindings               who holds which role, and where
```

---

## The tables

### `permissions` — the vocabulary

One column, one row per permission string (`artifact:view`, `approve`, `model:manage`, …).
34 rows today.

Nothing can be granted unless it is here: both `role_permissions` and
`role_permission_overrides` have foreign keys onto it, so a typo cannot become a
permission. **This is the table you insert into to add a new permission** — see
[Adding a permission](#adding-a-new-permission).

### `roles` — the built-in roles

Name and description for the 13 roles: `org_admin`, `bu_admin`, `contributor`,
`project_admin`, `ba`, `architect`, `developer`, `qa`, `security_engineer`,
`devops_engineer`, `data_engineer`, `scrum_master`, `custom`.

`custom` is a placeholder that a custom-role binding carries; its real permissions live on
the custom role itself.

### `role_permissions` — the shipped default

The many-to-many between the two above: which permissions each built-in role ships with.
86 rows.

> **Do not edit this table by hand.** It is reconciled from `_ROLE_PERMISSIONS` in
> `backend/shared/authz/permissions.py` on every backend boot, and the reconcile *deletes*
> any row the code does not declare. A manual change survives until the next restart. To
> change what a role grants, use the Roles page (which writes the override table) or change
> the code matrix.

### `role_permission_overrides` — what YOUR org changed

Added in migration `0011`. Tenant-scoped, RLS-protected, and **never touched by the boot
reconcile** — which is the entire reason it is a separate table.

The Roles & Access page writes here. The rule is **whole-set replacement**: once a role has
any rows here, those rows *are* its permission set and its shipped defaults stop applying
entirely. There is no per-permission "granted/revoked" flag, because a delta has to answer
"what happens when the shipped default later gains a permission this org removed", and every
answer to that surprises somebody.

"Reset to defaults" on the Roles page deletes that role's rows here.

### `role_bindings` — who holds what, where

A person, a role, and a scope (`organization` / `business_unit` / `project`). This answers
*who*; the tables above answer *what*.

---

## Effective permissions

```
effective(role) = override rows for that role   if any exist
                  role_permissions rows          otherwise
```

Merged in exactly one place — `backend/shared/authz/role_permissions.py::effective_by_role`
— and **both** permission readers call it:

| reader | when |
|---|---|
| `resolve_permissions_for_user` | at login; the result goes into the JWT and the session |
| `can_perform._permissions_at` | on every scoped check |

They must agree. If only one applied overrides, a retuned role would work on one screen and
be refused on another *with the same token* — which presents as "works on the dashboard,
denied on the project page". `tests/test_role_permission_overrides.py::test_both_permission_paths_agree`
pins this.

---

## How a fresh database gets its permissions

**Automatically, on first backend boot — not by migration.**

1. `alembic upgrade head` creates the tables. They are **empty**; no migration inserts
   catalogue rows.
2. The backend starts. In its lifespan, `assert_rbac_catalog` counts `roles`.
3. **Count is 0** → this is a fresh database, so it calls `seed_rbac_catalog`, which inserts
   every permission, role and role-permission edge from the code matrix. You get a fully
   populated catalogue with no manual step.
4. **Count is > 0** → it *verifies* instead, comparing the database against the code.

So: create the DB, migrate, start the backend once. Done.

`role_permission_overrides` is **not** seeded and starts empty — correctly, since empty means
"no role has been retuned here".

### The boot guard, and when it stops your backend starting

On an already-populated database, any difference between the code matrix and the tables is
treated as **tampering, not drift to fix**:

```
RbacCatalogDriftError: N RBAC catalogue difference(s) between the database and the code.
Refusing to start: a role's permissions must come from the code, never from a direct
database write.
```

This is deliberate. `role_permissions` is a *global* table with no tenant column — a direct
INSERT there escalates every holder of that role in every tenant at once, so a mismatch is
worth halting for.

You will hit this if you edit `_ROLE_PERMISSIONS` in code and restart without reconciling.
Two ways out:

```bash
# Preferred: reconcile once, explicitly.
cd backend && .venv/Scripts/python.exe -c "
import sys, asyncio; sys.path.insert(0,'.')
from shared.authz.catalog import seed_rbac_catalog
from shared.db import get_db_session_superuser
async def m():
    async with get_db_session_superuser() as s:
        print(await seed_rbac_catalog(s))
asyncio.run(m())"
```

```bash
# Or let every boot repair itself. Off by default on purpose: repairing silently
# would remove the alarm, which is the point of the check.
RBAC_CATALOG_AUTOREPAIR=true
```

Check for drift before restarting:

```bash
cd backend && .venv/Scripts/python.exe -c "
import sys, asyncio; sys.path.insert(0,'.')
from shared.authz.catalog import verify_rbac_catalog
from shared.db import get_db_session_superuser
async def m():
    async with get_db_session_superuser() as s:
        print(await verify_rbac_catalog(s) or 'no drift')
asyncio.run(m())"
```

---

## Adding a new permission

Two steps, and the first one alone already works:

**1. Insert it.** It is immediately selectable on the Roles page and grantable to a custom
role — no deploy, no restart.

```sql
INSERT INTO permissions (name) VALUES ('deploy:approve')
ON CONFLICT (name) DO NOTHING;
```

`GET /admin/permissions` serves this table, so the UI picks it up on the next load. Until
somebody writes a label for it, it appears under **Other** by its raw id.

**2. Optionally, give it a home in code** so it reads properly and can ship as a default:

- `frontend/lib/auth/permission-catalog.ts` — group, label, one-line description.
- `backend/shared/authz/permissions.py` — add to `_PERMISSION_CATALOG`, and to any role in
  `_ROLE_PERMISSIONS` that should ship with it. **This changes the shipped default, so the
  boot guard will demand a reconcile.**

Wildcards (`admin:*`) are filtered out of `GET /admin/permissions`: they satisfy every check,
so offering one in a picker is offering "make this an administrator" disguised as a checkbox.

---

## Queries

Connect:

```bash
psql -h localhost -p 5432 -U postgres -d sdlc_product     # local dev password: 1234
```

> **RLS warning.** `role_permission_overrides` and `role_bindings` are FORCE row-level
> security. Querying them as `sdlc_app` without a tenant returns **zero rows**, which looks
> identical to "nothing is configured". Set the tenant first:
>
> ```sql
> SET app.current_tenant_id = 'd26c42ac-52f0-407c-baaf-fd720821913e';
> ```
>
> Find your org id with `SELECT id, slug, display_name FROM organizations;`.

### The permission catalogue

```sql
SELECT name FROM permissions ORDER BY name;
```

### Roles, with how many permissions each ships with

```sql
SELECT r.name,
       r.description,
       count(rp.permission_name) AS default_permissions
FROM roles r
LEFT JOIN role_permissions rp ON rp.role_name = r.name
GROUP BY r.name, r.description
ORDER BY r.name;
```

### What one role ships with

```sql
SELECT permission_name
FROM role_permissions
WHERE role_name = 'developer'
ORDER BY permission_name;
```

### Everything your org has retuned

```sql
SELECT role_name, permission_name, updated_by, updated_at
FROM role_permission_overrides
ORDER BY role_name, permission_name;
```

Empty result = nothing has been changed from the Roles page.

### Effective permissions — what actually decides access

```sql
SELECT r.name AS role,
       COALESCE(o.permission_name, rp.permission_name) AS permission,
       (o.permission_name IS NOT NULL) AS overridden
FROM roles r
LEFT JOIN role_permission_overrides o
       ON o.role_name = r.name
      AND o.tenant_id = 'd26c42ac-52f0-407c-baaf-fd720821913e'
LEFT JOIN role_permissions rp
       ON rp.role_name = r.name
      AND NOT EXISTS (SELECT 1 FROM role_permission_overrides x
                       WHERE x.role_name = r.name
                         AND x.tenant_id = 'd26c42ac-52f0-407c-baaf-fd720821913e')
WHERE COALESCE(o.permission_name, rp.permission_name) IS NOT NULL
ORDER BY r.name, permission;
```

The `NOT EXISTS` clause *is* the merge rule: once a role has any override rows, its defaults
stop contributing entirely.

### Which roles differ from their shipped defaults

```sql
SELECT DISTINCT role_name
FROM role_permission_overrides
ORDER BY role_name;
```

### One person's effective permissions

```sql
SELECT DISTINCT COALESCE(o.permission_name, rp.permission_name) AS permission
FROM role_bindings rb
LEFT JOIN role_permission_overrides o
       ON o.role_name = rb.role_name AND o.tenant_id = rb.tenant_id
LEFT JOIN role_permissions rp
       ON rp.role_name = rb.role_name
      AND NOT EXISTS (SELECT 1 FROM role_permission_overrides x
                       WHERE x.role_name = rb.role_name AND x.tenant_id = rb.tenant_id)
WHERE rb.user_id = (SELECT id FROM users WHERE email = 'diego@abcbank.com')
  AND rb.status = 'active'
ORDER BY permission;
```

### Who holds what, and where

```sql
SELECT u.email,
       rb.role_name,
       rb.scope_kind,
       COALESCE(w.display_name, p.display_name, o.display_name) AS scope,
       rb.status
FROM role_bindings rb
JOIN users u          ON u.id = rb.user_id
LEFT JOIN workspaces w    ON w.id = rb.scope_id
LEFT JOIN projects p      ON p.id = rb.scope_id
LEFT JOIN organizations o ON o.id = rb.scope_id
ORDER BY u.email, rb.scope_kind;
```

### Custom roles and what they grant

```sql
SELECT cr.name, cr.scope_kind, crp.permission_name
FROM custom_roles cr
LEFT JOIN custom_role_permissions crp ON crp.custom_role_id = cr.id
ORDER BY cr.name, crp.permission_name;
```

Custom roles are not overridable — editing one *is* editing its permissions, so there is no
default for it to diverge from.

### Who changed a role, and when

```sql
SELECT created_at, actor_id, event_type, resource_id, payload
FROM audit_events
WHERE event_type LIKE 'role_permissions.%'
ORDER BY created_at DESC;
```

Worth knowing this exists: redefining a role changes what *every* holder may do without
touching a single binding, so it leaves no trace in the assignment history — this is the
only place it shows up.

---

## Related

- `backend/shared/authz/permissions.py` — the code matrix, the source of the defaults.
- `backend/shared/authz/catalog.py` — the seeder and the boot guard.
- `backend/shared/authz/role_permissions.py` — the default/override merge.
- `backend/shared/routers/role_permissions.py` — the endpoints the Roles page calls.
- `backend/migrations/versions/0011_role_permission_overrides.py` — why the split exists.
