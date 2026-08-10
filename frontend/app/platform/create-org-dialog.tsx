"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Building2, CheckCircle2, Eye, EyeOff, Loader2, Plus } from "lucide-react";

import { ApiRequestError } from "@/lib/api/client";
import { createOrganization } from "@/lib/api/platform-client";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 128);
}

export function CreateOrgDialog() {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugEdited, setSlugEdited] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [created, setCreated] = React.useState<{ name: string; email: string } | null>(null);

  const effectiveSlug = slugEdited ? slug : slugify(name);

  function reset() {
    setName("");
    setSlug("");
    setSlugEdited(false);
    setEmail("");
    setPassword("");
    setShowPassword(false);
    setError(null);
    setSubmitting(false);
    setCreated(null);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await createOrganization({
        displayName: name,
        slug: effectiveSlug,
        adminEmail: email,
        adminPassword: password,
      });
      setCreated({ name: result.display_name, email: result.admin_email });
      router.refresh(); // re-render the server org list
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setError(
          err.status === 409
            ? "That slug is already taken — try another."
            : err.status === 422
              ? "Check the fields: a valid email and an 8+ character password are required."
              : "Couldn't create the organization. Please try again.",
        );
      } else {
        setError("Something went wrong. Please try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="gap-2">
          <Plus className="size-4" aria-hidden />
          New organization
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CheckCircle2 className="text-primary size-5" aria-hidden />
                Organization created
              </DialogTitle>
              <DialogDescription>
                <span className="text-foreground font-medium">{created.name}</span> is ready.
              </DialogDescription>
            </DialogHeader>
            <Alert>
              <AlertDescription className="text-sm">
                The org admin is{" "}
                <span className="text-foreground font-medium">{created.email}</span>. Share the
                password you just set with them <strong>securely</strong> — it isn&apos;t shown
                again. They can change it after first sign-in.
              </AlertDescription>
            </Alert>
            <DialogFooter>
              <Button variant="outline" onClick={reset}>
                Create another
              </Button>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={onSubmit}>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Building2 className="size-5" aria-hidden />
                New organization
              </DialogTitle>
              <DialogDescription>
                Provisions the org, a default {BUSINESS_UNIT_LABEL.toLowerCase()}, and its first admin
                account.
              </DialogDescription>
            </DialogHeader>

            <div className="flex flex-col gap-4 py-4">
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="org-name">Organization name</Label>
                <Input
                  id="org-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Acme Corp"
                  required
                  disabled={submitting}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="org-slug">Slug</Label>
                <Input
                  id="org-slug"
                  value={effectiveSlug}
                  onChange={(e) => {
                    setSlugEdited(true);
                    setSlug(slugify(e.target.value));
                  }}
                  placeholder="acme-corp"
                  required
                  disabled={submitting}
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">
                  Lowercase, used in URLs. Auto-filled from the name.
                </p>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="org-admin-email">Admin email</Label>
                <Input
                  id="org-admin-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@acme.com"
                  required
                  disabled={submitting}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="org-admin-pw">Admin password</Label>
                <div className="relative">
                  <Input
                    id="org-admin-pw"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 8 characters"
                    minLength={8}
                    required
                    disabled={submitting}
                    className="pr-9"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    tabIndex={-1}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="text-muted-foreground hover:text-foreground absolute right-2.5 top-1/2 -translate-y-1/2 rounded-sm p-0.5 transition-colors"
                  >
                    {showPassword ? (
                      <EyeOff className="size-4" aria-hidden />
                    ) : (
                      <Eye className="size-4" aria-hidden />
                    )}
                  </button>
                </div>
              </div>
            </div>

            <DialogFooter>
              <Button type="submit" disabled={submitting} className="gap-2">
                {submitting ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                    Creating…
                  </>
                ) : (
                  "Create organization"
                )}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
