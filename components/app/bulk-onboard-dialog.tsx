"use client";

import * as React from "react";
import { AlertTriangle, Check, Download, FileSpreadsheet, Loader2, Upload, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { onboardPerson } from "@/lib/api/onboarding";
import { ORG_ASSIGNABLE_ROLES, ROLE_META, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { parseSpreadsheet, SpreadsheetError } from "@/lib/spreadsheet";

/** The four columns, and every spelling of them worth accepting. Matched
 *  case- and space-insensitively, because a roster exported from HR says
 *  "Email Address" and one typed by hand says "email". */
const COLUMN_ALIASES: Record<"email" | "name" | "role" | "unit", string[]> = {
  email: ["email", "emailaddress", "workemail", "mail"],
  name: ["name", "fullname", "displayname", "person"],
  role: ["role", "orgrole", "organizationrole", "appointment"],
  unit: ["businessunit", "unit", "bu", "department", "team"],
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, "");

/** Accept the stored value or the label — someone filling a template in reads
 *  "Business Unit Admin" off the header note, not `bu_admin`. */
function parseRole(raw: string): PlatformRole | null {
  const n = norm(raw);
  return (
    ORG_ASSIGNABLE_ROLES.find((r) => norm(r) === n || norm(ROLE_META[r].label) === n) ?? null
  );
}

interface ParsedRow {
  email: string;
  displayName: string;
  role: PlatformRole | null;
  unitId: string | null;
  unitRaw: string;
  /** Why this row cannot be onboarded, or null when it can. */
  problem: string | null;
  status: "pending" | "done" | "failed";
  error?: string;
}

export interface BulkOnboardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  businessUnits: readonly { id: string; displayName: string }[];
  onFinished: () => void;
}

/**
 * Onboard a roster from a spreadsheet.
 *
 * VALIDATED BEFORE ANYTHING IS SENT, and shown row by row. A bulk import that
 * posts as it reads gets halfway through a hundred people and stops on a typo,
 * leaving nobody able to say which half landed. Here the file is parsed, every
 * row is checked against the same two rules the single-person dialog enforces,
 * and the admin sees exactly what will happen before it does.
 *
 * Bad rows are SKIPPED, not blocking. A roster with one malformed email should
 * still onboard the other ninety-nine — the alternative is an admin editing a
 * spreadsheet to satisfy a dialog.
 */
export function BulkOnboardDialog({
  open,
  onOpenChange,
  businessUnits,
  onFinished,
}: BulkOnboardDialogProps) {
  const [rows, setRows] = React.useState<ParsedRow[]>([]);
  const [fileName, setFileName] = React.useState<string | null>(null);
  const [parseError, setParseError] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState(false);
  const [finished, setFinished] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (open) {
      setRows([]);
      setFileName(null);
      setParseError(null);
      setRunning(false);
      setFinished(false);
    }
  }, [open]);

  const unitByName = React.useMemo(
    () => new Map(businessUnits.map((u) => [norm(u.displayName), u.id])),
    [businessUnits],
  );

  async function handleFile(file: File) {
    setParseError(null);
    setFinished(false);
    setFileName(file.name);
    try {
      const table = await parseSpreadsheet(file);
      if (table.length < 2) {
        setParseError("That file has a header row and nothing under it.");
        setRows([]);
        return;
      }

      const header = table[0]!.map(norm);
      const columnFor = (key: keyof typeof COLUMN_ALIASES) =>
        header.findIndex((h) => COLUMN_ALIASES[key].includes(h));
      const cols = {
        email: columnFor("email"),
        name: columnFor("name"),
        role: columnFor("role"),
        unit: columnFor("unit"),
      };

      if (cols.email === -1 || cols.role === -1) {
        setParseError(
          "The first row has to name the columns — at least Email and Role. Download the template if you're not sure.",
        );
        setRows([]);
        return;
      }

      const seen = new Set<string>();
      const parsed = table.slice(1).map<ParsedRow>((cells) => {
        const at = (i: number) => (i >= 0 ? (cells[i] ?? "").trim() : "");
        const email = at(cols.email);
        const unitRaw = at(cols.unit);
        const role = parseRole(at(cols.role));
        const unitId = unitRaw ? (unitByName.get(norm(unitRaw)) ?? null) : null;

        let problem: string | null = null;
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) problem = "Not a valid email";
        else if (seen.has(email.toLowerCase())) problem = "Listed twice in this file";
        else if (!role)
          problem = `Role must be ${ROLE_META.bu_admin.label} or ${ROLE_META.contributor.label}`;
        else if (unitRaw && !unitId) problem = `No ${BUSINESS_UNIT_LABEL.toLowerCase()} called "${unitRaw}"`;
        else if (role === "contributor" && !unitId)
          problem = `A ${ROLE_META.contributor.label} needs a ${BUSINESS_UNIT_LABEL.toLowerCase()}`;
        if (!problem) seen.add(email.toLowerCase());

        return {
          email,
          displayName: at(cols.name),
          role,
          unitId,
          unitRaw,
          problem,
          status: "pending",
        };
      });
      setRows(parsed);
    } catch (err) {
      setParseError(
        err instanceof SpreadsheetError ? err.message : "Couldn't read that file.",
      );
      setRows([]);
    }
  }

  const ready = rows.filter((r) => r.problem === null);
  const skipped = rows.length - ready.length;

  async function run() {
    setRunning(true);
    // Sequential, not parallel. Every row hits `findOrCreateIdentity`, and two
    // rows for the same new person racing each other would mint two.
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i]!;
      if (row.problem !== null) continue;
      try {
        await onboardPerson({
          email: row.email,
          displayName: row.displayName || undefined,
          workspaceId: row.unitId ?? undefined,
          role: row.role!,
        });
        setRows((prev) => prev.map((r, j) => (j === i ? { ...r, status: "done" } : r)));
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed";
        setRows((prev) =>
          prev.map((r, j) => (j === i ? { ...r, status: "failed", error: message } : r)),
        );
      }
    }
    setRunning(false);
    setFinished(true);
    onFinished();
  }

  function downloadTemplate() {
    const example = businessUnits[0]?.displayName ?? "Payments";
    const csv = [
      "Email,Name,Role,Business Unit",
      `jane.doe@company.com,Jane Doe,${ROLE_META.contributor.label},${example}`,
      `sam.lee@company.com,Sam Lee,${ROLE_META.bu_admin.label},`,
    ].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "onboarding-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  const done = rows.filter((r) => r.status === "done").length;
  const failed = rows.filter((r) => r.status === "failed").length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display text-lg font-bold tracking-tight">
            Onboard from a spreadsheet
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            Four columns: Email, Name, Role, {BUSINESS_UNIT_LABEL}. The same two roles as the
            single-person form — everything else stays the {BUSINESS_UNIT_LABEL.toLowerCase()}
            &apos;s to assign.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleFile(file);
                // Reset so re-picking the SAME file after a fix still fires.
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => inputRef.current?.click()}
              disabled={running}
              className="border-line-soft"
            >
              <Upload className="size-4" aria-hidden />
              Choose a file
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={downloadTemplate}
              className="text-muted-foreground"
            >
              <Download className="size-3.5" aria-hidden />
              Download template
            </Button>
            {fileName && (
              <span className="text-muted-foreground inline-flex items-center gap-1.5 font-mono text-[11px]">
                <FileSpreadsheet className="size-3.5" aria-hidden />
                {fileName}
              </span>
            )}
          </div>

          {parseError && (
            <p className="text-destructive flex items-start gap-1.5 text-[12px]">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {parseError}
            </p>
          )}

          {rows.length > 0 && (
            <>
              <div className="text-muted-foreground flex flex-wrap items-center gap-3 text-[12px]">
                <span>
                  <span className="text-foreground font-medium">{ready.length}</span> ready
                </span>
                {skipped > 0 && (
                  <span className="text-warning">
                    {skipped} will be skipped — the reason is on each row
                  </span>
                )}
                {finished && (
                  <span className="ml-auto">
                    {done} onboarded{failed > 0 ? `, ${failed} failed` : ""}
                  </span>
                )}
              </div>

              <ul className="border-line-soft divide-line-soft max-h-72 divide-y overflow-y-auto rounded-lg border">
                {rows.map((r, i) => (
                  <li
                    key={`${r.email}-${i}`}
                    className={cn(
                      "flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-[12px]",
                      r.problem && "bg-warning/5",
                      r.status === "failed" && "bg-destructive/5",
                    )}
                  >
                    <span className="w-5 shrink-0">
                      {r.status === "done" ? (
                        <Check className="text-success size-3.5" aria-hidden />
                      ) : r.status === "failed" || r.problem ? (
                        <X className="text-warning size-3.5" aria-hidden />
                      ) : (
                        <span className="text-muted-foreground font-mono text-[10px]">{i + 1}</span>
                      )}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {r.displayName ? `${r.displayName} · ` : ""}
                      <span className="text-muted-foreground">{r.email || "(no email)"}</span>
                    </span>
                    <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                      {r.role ? ROLE_META[r.role].shortLabel : "—"}
                      {r.unitRaw ? ` · ${r.unitRaw}` : ""}
                    </span>
                    {(r.problem || r.error) && (
                      <span className="text-warning w-full pl-8 text-[11px]">
                        {r.error ?? r.problem}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={running}
            className="border-line-soft"
          >
            {finished ? "Close" : "Cancel"}
          </Button>
          <Button
            onClick={() => void run()}
            disabled={ready.length === 0 || running || finished}
            aria-busy={running}
          >
            {running && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Onboard {ready.length || ""} {ready.length === 1 ? "person" : "people"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
