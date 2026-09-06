// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getGrantedProviders = vi.fn().mockResolvedValue([]);
vi.mock("@/lib/api/models", () => ({
  getModelAvailability: vi.fn().mockResolvedValue([]), // empty granted rows -> ungranted disclosure renders
  // Held providers. Separate from availability on purpose: a provider granted with
  // nothing curated under it yet has no model rows, so availability cannot report it.
  getGrantedProviders: (...args: unknown[]) => getGrantedProviders(...args),
}));

// RequestAccessButton renders nothing unless the acting role may raise the
// request type it was given (`canRaiseType`), so without this the whole tree
// under test (including RaiseRequestDialog) never mounts. `audience="bu"`
// below routes to `model_provider_access`, which only a bu_admin may raise.
vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => ({ role: "bu_admin" }),
}));

// A plain stub, not a rendered dialog — RequestAccessButton always mounts
// RaiseRequestDialog (open={false} until clicked), so its prefill prop is
// observable on render without needing to open anything. This is a simpler,
// more idiomatic mock than spy-wrapping the real component: it directly
// mirrors provider-detail-rbac-gate.test.tsx's "stub the module, assert on
// what reached it" style rather than inventing a new technique.
const raiseRequestDialogSpy = vi.fn();
vi.mock("@/components/requests/raise-request-dialog", () => ({
  RaiseRequestDialog: (props: unknown) => {
    raiseRequestDialogSpy(props);
    return null;
  },
}));

import { ModelAvailabilityCard } from "@/components/app/model-availability-card";

afterEach(() => {
  cleanup();
  getGrantedProviders.mockResolvedValue([]);
});

function renderCard(props: Partial<React.ComponentProps<typeof ModelAvailabilityCard>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelAvailabilityCard
        workspaceId="ws-1"
        workspaceName="Payments"
        audience="bu"
        catalog={[
          {
            provider: "anthropic",
            label: "Anthropic",
            models: [{ model_id: "claude-sonnet-5", label: "Claude Sonnet 5" }],
          },
        ]}
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("model availability card — request prefill", () => {
  it("asks for the PROVIDER, with no modelId, when nothing from it reaches the unit", async () => {
    // The list used to be per MODEL — ~2,700 rows, one request each — which asked for
    // something the approval screen could not give: an Org Admin grants a provider to
    // a unit and curates its models afterwards. So the request names the provider only.
    renderCard();
    const disclosure = await screen.findByText(/provider/i);
    await userEvent.click(disclosure);
    const lastCall = raiseRequestDialogSpy.mock.calls.at(-1)?.[0] as { prefill?: unknown };
    expect(lastCall?.prefill).toMatchObject({
      type: "model_provider_access",
      providerModel: { provider: "anthropic" },
    });
    expect((lastCall?.prefill as { providerModel?: Record<string, unknown> })?.providerModel)
      .not.toHaveProperty("modelId");
  });

  it("collapses a provider's many models into one request, not one per model", async () => {
    renderCard({
      catalog: [
        {
          provider: "anthropic",
          label: "Anthropic",
          models: [
            { model_id: "claude-sonnet-5", label: "Claude Sonnet 5" },
            { model_id: "claude-opus-4-8", label: "Claude Opus 4.8" },
            { model_id: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
          ],
        },
      ],
    });
    // One disclosure row for the provider, naming how many models it covers.
    expect(await screen.findByText(/1 provider exist/i)).toBeInTheDocument();
    expect(await screen.findByText(/3 models/i)).toBeInTheDocument();
  });
  it("does not offer a provider the unit already holds", async () => {
    // The regression: "no models we can see" was read as "not granted", so a unit whose
    // request had just been approved was invited to request the same provider again.
    getGrantedProviders.mockResolvedValue([{ provider: "anthropic", curatedCount: 0 }]);
    renderCard();
    expect(await screen.findByText(/no models chosen yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/provider exist/i)).not.toBeInTheDocument();
  });

  it("names the granted-but-uncurated state instead of showing nothing", async () => {
    getGrantedProviders.mockResolvedValue([{ provider: "anthropic", curatedCount: 0 }]);
    renderCard();
    expect(await screen.findByText("Anthropic")).toBeInTheDocument();
    expect(await screen.findByText(/granted to this business unit/i)).toBeInTheDocument();
  });
});
