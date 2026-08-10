"""Read-only inspection of the multi-tenancy RBAC schema (Phases 1 & 2).

Run from platform/backend:
    python -m scripts.verify_tenancy

Uses the app's own DB resolver (Key Vault / env), exactly like alembic and the
tests. Makes NO writes — pure inspection. In the dev env the DB role is the
`postgres` superuser, so this reads across all tenants for inspection.
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from shared.db import get_db_session_superuser  # noqa: E402

RBAC_TABLES = [
    "organizations", "workspaces",
    "roles", "permissions", "role_permissions",
    "custom_roles", "custom_role_permissions",
    "user_workspace_roles", "users",
]
RLS_TABLES = [
    "projects", "runs", "artifacts", "audit_events", "agent_call_logs",
    "eval_records", "custom_roles", "custom_role_permissions", "user_workspace_roles",
]


async def main() -> None:
    async with get_db_session_superuser() as s:
        head = (await s.execute(text("select version_num from alembic_version"))).scalar()
        print(f"\n=== ALEMBIC HEAD: {head} ===  (expect 0012)")

        print("\n=== TABLE ROW COUNTS ===")
        for t in RBAC_TABLES:
            try:
                n = (await s.execute(text(f"select count(*) from {t}"))).scalar()
                print(f"  {t:<26} {n}")
            except Exception as e:  # noqa: BLE001
                print(f"  {t:<26} MISSING ({type(e).__name__})")

        print("\n=== DEFAULT ROLES -> PERMISSIONS (global catalog) ===")
        rows = (await s.execute(text(
            "select r.name, "
            "coalesce(string_agg(rp.permission_name, ', ' order by rp.permission_name), '(none)') "
            "from roles r left join role_permissions rp on rp.role_name = r.name "
            "group by r.name order by r.name"
        ))).fetchall()
        for name, perms in rows:
            print(f"  {name:<18} {perms}")

        cat = (await s.execute(text("select name from permissions order by name"))).scalars().all()
        print(f"\n=== PERMISSION CATALOG ({len(cat)}) ===\n  " + ", ".join(cat))

        print("\n=== ROW-LEVEL SECURITY (enabled / forced) ===")
        rls = (await s.execute(text(
            "select relname, relrowsecurity, relforcerowsecurity from pg_class "
            "where relname = any(:t) order by relname"
        ), {"t": RLS_TABLES})).fetchall()
        for name, ena, force in rls:
            flag = "OK" if (ena and force) else "!!! NOT FORCED"
            print(f"  {name:<26} enabled={str(ena):<5} forced={str(force):<5} {flag}")

        print("\n=== RLS POLICIES on custom-role tables ===")
        pol = (await s.execute(text(
            "select tablename, policyname, cmd, coalesce(qual, with_check) "
            "from pg_policies where tablename in ('custom_roles','custom_role_permissions') "
            "order by tablename, policyname"
        ))).fetchall()
        for tbl, pname, cmd, expr in pol:
            print(f"  {tbl}.{pname} [{cmd}]  {expr}")

        print("\n=== user_workspace_roles COLUMNS ===")
        cols = (await s.execute(text(
            "select column_name, data_type, is_nullable from information_schema.columns "
            "where table_name='user_workspace_roles' order by ordinal_position"
        ))).fetchall()
        for c, dt, nul in cols:
            print(f"  {c:<16} {dt:<28} nullable={nul}")

        print("\n=== user_workspace_roles CONSTRAINTS ===")
        cons = (await s.execute(text(
            "select conname, pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'user_workspace_roles'::regclass order by conname"
        ))).fetchall()
        for cn, cd in cons:
            print(f"  {cn}: {cd}")

        print("\n=== DONE ===\n")


if __name__ == "__main__":
    asyncio.run(main())
