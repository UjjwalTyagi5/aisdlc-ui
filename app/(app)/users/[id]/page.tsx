"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FolderKanban, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { getUserDetail } from "@/lib/api/users";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { RoleSummary, UserDetailBinding } from "@/lib/schemas/user-directory";

const RISE = {
  animationName: "rise",
  animationDuration: "0.55s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both",
} as const;

const STATUS_LABEL: Record<UserDetailBinding["status"], string> = {
  active: "Active",
  invited: "Invited",
  deactivated: "Deactivated",
};

export default function UserDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const userId = decodeURIComponent(id);

  const detailQ = useQuery({
    queryKey: qk.users.detail(userId),
    queryFn: () => getUserDetail(userId),
  });

  if (detailQ.isLoading) {
    return (
      <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={4} />
      </div>
    );
  }

  if (detailQ.isError || !detailQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load this person"
          description="They may have been removed, or you don't have access."
          onRetry={() => router.push("/users")}
          retryLabel="Back to Users"
        />
      </div>
    );
  }

  const person = detailQ.data;
  const roleLabels = new Map(person.roleSummaries.map((rs) => [rs.role, rs.label]));

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8" style={RISE}>
      <Link
        href="/users"
        className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 font-mono text-[11px] tracking-[0.12em] uppercase transition-colors"
      >
        <ArrowLeft className="size-3.5" aria-hidden />
        Users
      </Link>

      <div className="flex items-center gap-3">
        <span
          className="bg-surface-2 text-foreground/80 flex size-12 shrink-0 items-center justify-center rounded-full font-mono text-[15px] font-medium"
          aria-hidden
        >
          {person.initials}
        </span>
        <div>
          <h1 className="font-display text-[26px] font-bold leading-tight tracking-[-0.02em]">
            {person.displayName}
          </h1>
          {person.email && (
            <p className="text-muted-foreground text-[13px]">{person.email}</p>
          )}
        </div>
      </div>

      <BindingSection
        title={`${BUSINESS_UNIT_LABEL} membership`}
        bindings={person.workspaceBindings}
        emptyText={`Not a member of any ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
        roleLabels={roleLabels}
      />

      <BindingSection
        title="Project membership"
        bindings={person.projectBindings}
        emptyText="Not a member of any project."
        roleLabels={roleLabels}
        showParent
      />

      <RoleSummarySection roleSummaries={person.roleSummaries} />
    </div>
  );
}

function BindingSection({
  title,
  bindings,
  emptyText,
  roleLabels,
  showParent = false,
}: {
  title: string;
  bindings: UserDetailBinding[];
  emptyText: string;
  roleLabels: Map<string, string>;
  showParent?: boolean;
}) {
  return (
    <section
      className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
      style={{ ...RISE, animationDelay: "0.04s" }}
    >
      <div className="border-line-soft border-b px-6 py-4">
        <span className="font-display text-[15px] font-bold tracking-[-0.01em]">{title}</span>
      </div>
      {bindings.length === 0 ? (
        <p className="text-muted-foreground px-6 py-6 text-[13px]">{emptyText}</p>
      ) : (
        <ul className="divide-line-soft divide-y">
          {bindings.map((b) => (
            <li
              key={`${b.scope}-${b.id}`}
              className="flex flex-wrap items-center justify-between gap-2 px-6 py-3"
            >
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium">
                  {b.name}
                  {showParent && b.parentName && (
                    <span className="text-muted-foreground font-normal"> · {b.parentName}</span>
                  )}
                </p>
                {b.status !== "active" && (
                  <span className="text-muted-foreground font-mono text-[10.5px] uppercase tracking-wider">
                    {STATUS_LABEL[b.status]}
                  </span>
                )}
              </div>
              <Badge variant="secondary" className="shrink-0 font-mono text-[11px]">
                {roleLabels.get(b.role) ?? b.role.replace(/_/g, " ")}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RoleSummarySection({ roleSummaries }: { roleSummaries: RoleSummary[] }) {
  return (
    <section className="space-y-3" style={{ ...RISE, animationDelay: "0.08s" }}>
      <h2 className="font-display text-[15px] font-bold tracking-[-0.01em]">
        Permissions &amp; access levels
      </h2>
      {roleSummaries.length === 0 ? (
        <p className="text-muted-foreground text-[13px]">
          No role held anywhere yet — nothing to resolve.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {roleSummaries.map((rs) => (
            <div
              key={rs.role}
              className="border-line-soft bg-panel-elevated space-y-3 rounded-2xl border p-4"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className={
                    rs.tier === "governance"
                      ? "text-brand-bright flex items-center gap-1 text-[13.5px] font-semibold"
                      : "flex items-center gap-1 text-[13.5px] font-semibold"
                  }
                >
                  {rs.tier === "governance" && <ShieldCheck className="size-3.5" aria-hidden />}
                  {rs.label}
                </span>
                {rs.isCustom && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    Custom
                  </Badge>
                )}
              </div>

              {rs.permissions.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-widest uppercase">
                    Permissions
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {rs.permissions.map((p) => (
                      <Badge
                        key={p.id}
                        variant="secondary"
                        className="font-mono text-[10px]"
                        title={p.grants ?? undefined}
                      >
                        {p.label}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {rs.agentAccess.length > 0 && (
                <div>
                  <p className="text-muted-foreground mb-1.5 flex items-center gap-1 font-mono text-[10px] tracking-widest uppercase">
                    <FolderKanban className="size-3" aria-hidden />
                    Agent access
                  </p>
                  <ul className="space-y-1">
                    {rs.agentAccess.map((row) => (
                      <li
                        key={row.phase}
                        className="flex items-center justify-between gap-2 text-[12px]"
                      >
                        <span>{row.label}</span>
                        <span className="text-muted-foreground font-mono text-[10.5px] capitalize">
                          {row.level}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
