// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/models", () => ({
  getModelAvailability: vi.fn().mockResolvedValue([]), // empty granted rows -> ungranted disclosure renders
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

afterEach(cleanup);

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
  it("carries providerModel (not a row id) when requesting an ungranted model", async () => {
    renderCard();
    const disclosure = await screen.findByText(/more model/i);
    await userEvent.click(disclosure);
    const lastCall = raiseRequestDialogSpy.mock.calls.at(-1)?.[0] as { prefill?: unknown };
    expect(lastCall?.prefill).toMatchObject({
      type: "model_provider_access",
      providerModel: { provider: "anthropic", modelId: "claude-sonnet-5" },
    });
  });
});
