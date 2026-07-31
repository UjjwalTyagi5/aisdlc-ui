"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { TraceDetail } from "@/components/app/trace-detail";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { getTrace } from "@/lib/api/traces";
import { qk } from "@/lib/api/query-keys";

export default function TraceDetailPage() {
  const session = useSession({ required: true });
  const params = useParams<{ id: string }>();
  const id = params.id;

  const traceQ = useQuery({
    queryKey: qk.traces.detail(id),
    queryFn: () => getTrace(id),
    enabled: !!id,
  });

  if (!hasPermission(session, "trace:view")) {
    return (
      <RestrictedAccess description="Traces require the trace:view permission. Ask your admin for access." />
    );
  }

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link href="/traces">
          <ArrowLeft className="size-4" aria-hidden />
          Back to traces
        </Link>
      </Button>

      {traceQ.isLoading ? (
        <LoadingState variant="list" rows={5} />
      ) : traceQ.isError ? (
        <ApiErrorState
          title="Couldn't load trace"
          error={
            traceQ.error && "code" in traceQ.error && "message" in traceQ.error
              ? (traceQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(traceQ.error && "code" in traceQ.error)
              ? traceQ.error instanceof Error
                ? traceQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => traceQ.refetch()}
        />
      ) : traceQ.data ? (
        <TraceDetail trace={traceQ.data} />
      ) : null}
    </div>
  );
}
