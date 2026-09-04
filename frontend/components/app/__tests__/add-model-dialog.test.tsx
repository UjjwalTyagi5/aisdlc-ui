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
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
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

/**
 * `page.tsx` renders ONE persistent `AddModelDialog` instance and toggles it
 * by changing `initialProvider`/`open` as props — it never unmounts/remounts
 * the component the way every case above (which always mounts fresh with
 * `initialProvider` already set) does. That distinction hid a real bug: the
 * `provider` state only ever tracked `initialProvider` via the CLOSE-time
 * reset effect, so the first open ever — or an open whose `initialProvider`
 * changed since the dialog was last closed (e.g. "Add key" clicked on a
 * different row without closing first) — left `provider` stuck stale, and
 * every step after "Provider" (gated on that state, not the prop) silently
 * never rendered, even though the locked-provider text itself looked correct.
 */
describe("bu-add-key mode — long-lived instance whose initialProvider changes", () => {
  it("renders the Models step on the very first open, not just on a second one", async () => {
    const onAdded = vi.fn();
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <AddModelDialog
        open={false}
        onOpenChange={onOpenChange}
        catalog={catalog}
        catalogLoading={false}
        targetUnits={null}
        allowedByUnit={{}}
        fullCatalog={catalog}
        needsApproval={false}
        grantableWorkspaces={null}
        initialProvider={null}
        mode="org"
        onAdded={onAdded}
      />,
    );

    // Simulate clicking "Add key" on a granted row: page.tsx flips both
    // `open` and `initialProvider` on the SAME already-mounted instance.
    rerender(
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
      />,
    );

    expect(
      await screen.findByRole("checkbox", { name: /claude-sonnet-5/ }),
    ).toBeInTheDocument();
  });

  it("switches to the second row's provider without the dialog ever closing", async () => {
    const catalogTwoProviders: CatalogProvider[] = [
      ...catalog,
      {
        provider: "openai",
        label: "OpenAI",
        models: [
          { model_id: "gpt-5.1", label: "GPT-5.1", input_price_per_million: 2, output_price_per_million: 8 },
        ],
      },
    ];
    const onAdded = vi.fn();
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <AddModelDialog
        open
        onOpenChange={onOpenChange}
        catalog={catalogTwoProviders}
        catalogLoading={false}
        targetUnits={null}
        allowedByUnit={{}}
        fullCatalog={catalogTwoProviders}
        needsApproval={false}
        grantableWorkspaces={null}
        initialProvider="anthropic"
        mode="bu-add-key"
        onAdded={onAdded}
      />,
    );
    expect(await screen.findByRole("checkbox", { name: /claude-sonnet-5/ })).toBeInTheDocument();

    // Still open — the row that was clicked next belongs to a different
    // provider, same as clicking a different granted row's "Add key" while
    // this instance was never closed in between.
    rerender(
      <AddModelDialog
        open
        onOpenChange={onOpenChange}
        catalog={catalogTwoProviders}
        catalogLoading={false}
        targetUnits={null}
        allowedByUnit={{}}
        fullCatalog={catalogTwoProviders}
        needsApproval={false}
        grantableWorkspaces={null}
        initialProvider="openai"
        mode="bu-add-key"
        onAdded={onAdded}
      />,
    );

    expect(await screen.findByRole("checkbox", { name: /gpt-5\.1/ })).toBeInTheDocument();
  });
});

/**
 * An API base belongs to ONE vendor, so it must not survive changing vendor.
 *
 * Azure/Bedrock/Vertex REQUIRE an endpoint and the dialog invites one; Anthropic
 * needs none and renders the same field as an optional override. Switching
 * provider cleared the model selection but left the URL sitting there, so a
 * correct Anthropic key was tested against the Azure endpoint, came back 404
 * "Resource not found", and the dialog reported "Key rejected — verification
 * failed". Reported live: a valid key that could not be added.
 */
describe("switching provider clears the endpoint", () => {
  const twoProviders: CatalogProvider[] = [
    {
      provider: "azure",
      label: "Azure OpenAI",
      models: [
        {
          model_id: "azure/gpt-5-mini",
          label: "GPT-5 mini",
          input_price_per_million: 1,
          output_price_per_million: 4,
        },
      ],
    },
    ...catalog,
  ];

  it("does not carry an Azure endpoint over to Anthropic", async () => {
    const user = userEvent.setup();
    const props = {
      open: true,
      onOpenChange: vi.fn(),
      catalog: twoProviders,
      catalogLoading: false,
      targetUnits: null,
      allowedByUnit: {},
      fullCatalog: twoProviders,
      needsApproval: false,
      grantableWorkspaces: null,
      mode: "bu-add-key" as const,
      onAdded: vi.fn(),
    };
    // "Add key" on the Azure row: the dialog is long-lived, so this is a prop
    // change on a mounted component, not a fresh mount.
    const { rerender } = render(<AddModelDialog {...props} initialProvider="azure" />);

    await user.click(await screen.findByRole("checkbox", { name: /gpt-5-mini/ }));
    const base = await screen.findByLabelText(/api base/i);
    await user.type(base, "https://agenticaimodel.services.ai.azure.com");
    expect(base).toHaveValue("https://agenticaimodel.services.ai.azure.com");

    // "Add key" on the Anthropic row without ever closing the dialog — the exact
    // path that reached a real user, whose Anthropic key was then tested against
    // Azure's endpoint and reported as rejected.
    rerender(<AddModelDialog {...props} initialProvider="anthropic" />);

    await user.click(await screen.findByRole("checkbox", { name: /claude-sonnet-5/ }));
    await waitFor(() => expect(screen.getByLabelText(/api base/i)).toHaveValue(""));
  });
});

describe("a failed test reports the server's reason", () => {
  it("shows the endpoint diagnosis rather than blaming the key", async () => {
    vi.mocked(probeModelProvider).mockResolvedValue({
      status: "invalid",
      reason: "The API base is not a URL. Leave it blank to use the provider's own API.",
    } as Awaited<ReturnType<typeof probeModelProvider>>);

    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);
    await user.type(screen.getByLabelText(/api key/i), "sk-ant-whatever");
    await user.click(testButton());

    expect(await screen.findByText(/API base is not a URL/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Key rejected/)).not.toBeInTheDocument();
  });

  it("still says the key was rejected when that is what happened", async () => {
    vi.mocked(probeModelProvider).mockResolvedValue({
      status: "invalid",
      reason: "The provider rejected this key.",
    } as Awaited<ReturnType<typeof probeModelProvider>>);

    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);
    await user.type(screen.getByLabelText(/api key/i), "sk-ant-bad");
    await user.click(testButton());

    expect(await screen.findByText(/rejected this key/i)).toBeInTheDocument();
  });
});

/**
 * Chrome ignores autocomplete="off" on a password field: it sees the masked API key
 * input, decides the dialog is a sign-in form, and fills a saved password there plus
 * the account's username into the nearest text input above — the API base. Reported
 * live: an email address auto-filled into API base, sent as the endpoint, and a valid
 * Anthropic key was rejected for it.
 */
describe("browser password managers are kept out of the credential fields", () => {
  it("tells Chrome the API key is not a stored credential", async () => {
    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);

    const key = screen.getByLabelText(/api key/i);

    // "off" is the value Chrome overrides on password inputs; "new-password" is the
    // documented way to suppress filling a saved one.
    expect(key).toHaveAttribute("autocomplete", "new-password");
    expect(key).toHaveAttribute("data-1p-ignore");
    expect(key).toHaveAttribute("data-lpignore", "true");
  });

  it("keeps the username out of the API base, which is the field that got filled", async () => {
    const user = userEvent.setup();
    renderDialog();
    await pickFirstModel(user);

    const base = screen.getByLabelText(/api base/i);

    expect(base).toHaveAttribute("autocomplete", "off");
    expect(base).toHaveAttribute("data-1p-ignore");
    expect(base).toHaveAttribute("data-lpignore", "true");
  });
});
