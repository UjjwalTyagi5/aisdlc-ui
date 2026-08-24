// @vitest-environment jsdom
/**
 * BU Admin's "Add key" flow (spec §5, Task 10) — the parts of `AddModelDialog`
 * that only exist in `mode="bu-add-key"`:
 *
 *   - the key is REQUIRED (Save stays disabled while it's empty), unlike the
 *     Org Admin's onboarding, where an empty key is a real, save-able choice
 *   - Save stays disabled until "Test" reports the key actually works, rather
 *     than only being verified after the row (and its secret) are committed
 *   - there is no provider-picker step — the provider is fixed to whichever
 *     already-granted tile was clicked (`initialProvider`), never a question
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/api/models", () => ({
  addModelProvider: vi.fn(),
  verifyModelProvider: vi.fn(),
  probeModelProvider: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { AddModelDialog } from "@/components/app/add-model-dialog";
import { probeModelProvider } from "@/lib/api/models";
import type { CatalogProvider } from "@/lib/schemas/model";

afterEach(cleanup);

const catalog: CatalogProvider[] = [
  {
    provider: "anthropic",
    label: "Anthropic",
    models: [
      {
        model_id: "claude-sonnet-5",
        label: "Claude Sonnet 5",
        input_price_per_million: 3,
        output_price_per_million: 15,
      },
    ],
  },
];

function renderDialog(overrides: Partial<React.ComponentProps<typeof AddModelDialog>> = {}) {
  const onAdded = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <AddModelDialog
      open
      onOpenChange={onOpenChange}
      catalog={catalog}
      catalogLoading={false}
      targetUnits={null}
      allowedByUnit={{}}
      fullCatalog={catalog}
      needsApproval={false}
      grantableWorkspaces={null}
      initialProvider="anthropic"
      mode="bu-add-key"
      onAdded={onAdded}
      {...overrides}
    />,
  );
  return { onAdded, onOpenChange };
}

/**
 * Choosing a model reveals the Credential step, and also fills in the
 * subscription name — required in every mode (unrelated to this task: a
 * locked/preset provider, `bu-add-key` included, never auto-fills it the way
 * picking from the combobox does for the Org Admin's flow) — so every case
 * below can isolate what it's actually testing (the key/Test gating) instead
 * of tripping over this unrelated, always-required field.
 */
async function pickFirstModel(user: ReturnType<typeof userEvent.setup>) {
  const checkbox = await screen.findByRole("checkbox", { name: /claude-sonnet-5/ });
  await user.click(checkbox);
  await user.type(screen.getByLabelText(/subscription name/i), "Payments prod");
}

const saveButton = () => screen.getByRole("button", { name: /add key/i });
const testButton = () => screen.getByRole("button", { name: /^test$/i });

describe("bu-add-key mode — the key is required", () => {
  it("keeps Save disabled while the key is empty", async () => {
    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);
    expect(saveButton()).toBeDisabled();
  });

  it("still keeps Save disabled with a key typed but never tested", async () => {
    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);
    await user.type(screen.getByLabelText(/api key/i), "sk-untested");
    expect(saveButton()).toBeDisabled();
  });
});

describe("bu-add-key mode — Test gates Save", () => {
  it("enables Save only after Test reports the key valid", async () => {
    const user = userEvent.setup();
    vi.mocked(probeModelProvider).mockResolvedValue({ status: "valid" });
    renderDialog();
    await pickFirstModel(user);
    await user.type(screen.getByLabelText(/api key/i), "sk-good");
    expect(saveButton()).toBeDisabled();

    await user.click(testButton());
    await waitFor(() => expect(saveButton()).not.toBeDisabled());

    expect(probeModelProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: "anthropic",
        api_key: "sk-good",
        model: "claude-sonnet-5",
      }),
    );
  });

  it("keeps Save disabled when Test reports the key invalid", async () => {
    const user = userEvent.setup();
    vi.mocked(probeModelProvider).mockResolvedValue({ status: "invalid" });
    renderDialog();
    await pickFirstModel(user);
    await user.type(screen.getByLabelText(/api key/i), "sk-bad");

    await user.click(testButton());
    await waitFor(() => expect(screen.getByText(/key rejected/i)).toBeTruthy());
    expect(saveButton()).toBeDisabled();
  });

  it("re-closes Save once a previously-tested key is edited", async () => {
    const user = userEvent.setup();
    vi.mocked(probeModelProvider).mockResolvedValue({ status: "valid" });
    renderDialog();
    await pickFirstModel(user);
    const keyInput = screen.getByLabelText(/api key/i);
    await user.type(keyInput, "sk-good");
    await user.click(testButton());
    await waitFor(() => expect(saveButton()).not.toBeDisabled());

    // A stale "valid" must not vouch for a key that changed after the test.
    await user.type(keyInput, "-edited");
    expect(saveButton()).toBeDisabled();
  });
});

describe("bu-add-key mode — no provider picker", () => {
  it("shows the fixed provider as text, not a combobox to choose from", async () => {
    renderDialog();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByPlaceholderText("Search providers…")).toBeNull();
    expect(await screen.findByText("Anthropic")).toBeTruthy();
  });
});
