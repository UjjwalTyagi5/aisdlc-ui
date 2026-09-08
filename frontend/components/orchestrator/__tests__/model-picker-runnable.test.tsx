// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Orchestrator's model picker must offer only models that can actually RUN.
 *
 * Observed live on project `reall`: the picker opened on `ai21 · jamba-1.5-mini`,
 * a model with no credential and no valid provider connection, so the very first
 * message would have failed on model resolution.
 *
 * Two endpoints answer two different questions and the picker was reading the
 * wrong one:
 *
 *   `/model/allowed/project` — the GRANT cascade. What an admin ticked. Seven
 *      entries here, every one with `credentialId: null`, because nothing on that
 *      payload claims a key exists. `ai21` is simply first alphabetically.
 *   `/model/options`         — the RUNNABLE set. `model_offerings` joined to a
 *      provider connection that is keyed and verified, already intersected with
 *      that same cascade server-side. Two entries here.
 *
 * The fixtures below are the two live payloads, trimmed to their shape. The point
 * of pinning both is that the granted set stays deliberately WRONG and deliberately
 * FIRST: any implementation that reads it, or falls back to it, lands on `ai21`
 * and these tests say so.
 */

const ANTHROPIC_OFFERING = "cce7672c-anthropic-offering";
const AZURE_OFFERING = "10549e12-azure-offering";

/** `/model/options` — a keyed, valid provider connection serves each of these. */
const RUNNABLE = [
  {
    offering_id: ANTHROPIC_OFFERING,
    provider_id: "prov-anthropic-newkey",
    display_name: "Anthropicnewkwy",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    is_default: false,
  },
  {
    offering_id: AZURE_OFFERING,
    provider_id: "prov-azure-key",
    display_name: "Azure key",
    provider: "azure",
    model_id: "azure/gpt-5-mini",
    is_default: false,
  },
];

/** `/model/allowed/project` — what an admin ticked. Nothing here can be run as-is. */
const GRANTED = [
  { provider: "ai21", model_id: "jamba-1.5-mini", credentialId: null },
  { provider: "anthropic", model_id: "claude-opus-4-5", credentialId: null },
  { provider: "anthropic", model_id: "claude-opus-4-7", credentialId: null },
  { provider: "anthropic", model_id: "claude-opus-4-8", credentialId: null },
  { provider: "anthropic", model_id: "claude-sonnet-5", credentialId: null },
  { provider: "azure", model_id: "azure/gpt-5-mini", credentialId: null },
  { provider: "cohere_chat", model_id: "command-r-plus-08-2024", credentialId: null },
];

interface OptionsPayload {
  options: typeof RUNNABLE;
  default_offering_id: string | null;
  default_model_id: string | null;
}

let optionsPayload: OptionsPayload = {
  options: RUNNABLE,
  default_offering_id: null,
  default_model_id: null,
};

const getModelOptions = vi.fn(async (_projectId?: string) => optionsPayload);

/**
 * Both grant-cascade endpoints stay mocked and stay answerable.
 *
 * Not to let the picker use them — to make sure that if it does, it gets the
 * misleading answer the live app gave it, rather than an undefined that would
 * fail this suite for a reason unrelated to the defect.
 */
const getProjectModelSelection = vi.fn(async (_projectId: string) => ({
  inherited: GRANTED,
  inheritedFrom: { id: "58328103-bu", name: "PAYMENTS" },
  selected: GRANTED,
  usingDefaults: false,
  defaultKey: null,
}));

const getModelAvailability = vi.fn(async (_workspaceId: string) =>
  GRANTED.map((e) => ({
    ...e,
    credentialName: null,
    visibility: "global" as const,
    // The Business Unit answer, which is why it cannot stand in for the project
    // one: it is true for models this project has no keyed connection to.
    centrallyCredentialed: true,
    locallyCredentialed: false,
  })),
);

vi.mock("@/lib/api/models", () => ({
  getModelOptions: (projectId?: string) => getModelOptions(projectId),
  getProjectModelSelection: (projectId: string) => getProjectModelSelection(projectId),
  getModelAvailability: (workspaceId: string) => getModelAvailability(workspaceId),
}));

import { ModelPicker, type ProjectModelOption } from "@/components/orchestrator/model-picker";

const onOptionsResolved = vi.fn<(o: ProjectModelOption[], d: string | null) => void>();
const onValueChange = vi.fn();

function renderPicker(props: Partial<React.ComponentProps<typeof ModelPicker>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ModelPicker
        projectId="c3b0cd34-6657-4f91-b1f2-04f394502f81"
        value={null}
        onValueChange={onValueChange}
        onOptionsResolved={onOptionsResolved}
        {...props}
      />
    </QueryClientProvider>,
  );
}

/** The trigger, once the picker has stopped loading. */
async function trigger(): Promise<HTMLElement> {
  return await screen.findByRole("combobox", { name: /model/i });
}

/**
 * The last `(options, defaultKey)` the picker reported to the cockpit.
 *
 * Waits for the picker to leave its loading state first. It reports once on the
 * first render, before any request has resolved, and reading that call would pass
 * or fail on timing rather than on what the picker decided.
 */
async function lastResolved(): Promise<{ keys: string[]; defaultKey: string | null }> {
  await waitFor(() =>
    expect(screen.queryByText(/loading models/i)).not.toBeInTheDocument(),
  );
  await waitFor(() => expect(onOptionsResolved).toHaveBeenCalled());
  const calls = onOptionsResolved.mock.calls;
  const [options, defaultKey] = calls[calls.length - 1]!;
  return { keys: options.map((o) => o.key), defaultKey };
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  // jsdom has no ResizeObserver; Radix's Popover measures its content on open.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  getModelOptions.mockClear();
  getProjectModelSelection.mockClear();
  getModelAvailability.mockClear();
  onOptionsResolved.mockClear();
  onValueChange.mockClear();
  optionsPayload = {
    options: RUNNABLE,
    default_offering_id: null,
    default_model_id: null,
  };
});

afterEach(cleanup);

describe("Orchestrator ModelPicker — the offer is what can run", () => {
  it("does not open on a granted-but-unrunnable model", async () => {
    // The live defect, stated as narrowly as it happened.
    renderPicker();
    const el = await trigger();
    expect(el).not.toHaveTextContent(/jamba-1\.5-mini/);
    expect(el).not.toHaveTextContent(/ai21/);
  });

  it("opens on a model /model/options actually serves", async () => {
    renderPicker();
    await waitFor(() => expect(trigger()).resolves.toHaveTextContent(/claude-opus-4-5/));
  });

  it("offers the runnable models and nothing else", async () => {
    renderPicker();
    fireEvent.click(await trigger());

    // Scoped to the open popover: the trigger renders the selected model's id too.
    const list = within(await screen.findByRole("dialog"));
    expect(list.getByText("claude-opus-4-5")).toBeInTheDocument();
    expect(list.getByText("azure/gpt-5-mini")).toBeInTheDocument();
    // Granted, ticked by an admin, listed by `/model/allowed/project`, and unable
    // to answer a single message.
    expect(screen.queryByText("jamba-1.5-mini")).not.toBeInTheDocument();
    expect(screen.queryByText("claude-opus-4-7")).not.toBeInTheDocument();
    expect(screen.queryByText("command-r-plus-08-2024")).not.toBeInTheDocument();
  });

  it("reports only runnable models upward, so a run cannot be seeded with another", async () => {
    // `onOptionsResolved` is the damage path: the cockpit writes the reported
    // `defaultKey` straight into the session's model, and `createRun` sends it.
    renderPicker();
    const { keys, defaultKey } = await lastResolved();
    expect(keys).toHaveLength(2);
    expect(keys.some((k) => k.startsWith("ai21::"))).toBe(false);
    expect(defaultKey).not.toBeNull();
    expect(keys).toContain(defaultKey);
  });

  it("names the provider connection in each key, so the run resolves an exact offering", async () => {
    // The cockpit turns a model key into an `offering_id` by matching the key's
    // third segment against `provider_id`. Keys built from the grant cascade carry
    // an empty one (every entry there has `credentialId: null`), so the cockpit fell
    // back to "whichever offering serves this model first" — the wrong contract, and
    // the wrong region, whenever two connections serve the same model.
    renderPicker();
    const { keys } = await lastResolved();
    expect(keys).toContain("anthropic::claude-opus-4-5::prov-anthropic-newkey");
    expect(keys).toContain("azure::azure/gpt-5-mini::prov-azure-key");
  });

  it("never falls back to the granted set when nothing is runnable", async () => {
    // The tempting "show them something" fix. Every one of these fails on the model
    // call, so an empty picker is the honest answer and this is the regression that
    // would undo the whole change.
    optionsPayload = { options: [], default_offering_id: null, default_model_id: null };
    renderPicker();

    expect(await screen.findByText(/no model this project can run/i)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /model/i })).not.toBeInTheDocument();
    const { keys, defaultKey } = await lastResolved();
    expect(keys).toEqual([]);
    expect(defaultKey).toBeNull();
  });
});

describe("Orchestrator ModelPicker — which one is preselected", () => {
  it("honours the project's configured default offering", async () => {
    optionsPayload = {
      options: RUNNABLE,
      default_offering_id: AZURE_OFFERING,
      default_model_id: null,
    };
    renderPicker();
    await waitFor(() => expect(trigger()).resolves.toHaveTextContent(/azure\/gpt-5-mini/));
  });

  it("ignores a configured default that names an offering this project cannot run", async () => {
    // A default outlives the connection it named. Honouring it blindly reproduces
    // the exact defect — a preselected model absent from `options` — from a
    // different direction.
    optionsPayload = {
      options: RUNNABLE,
      default_offering_id: "offering-that-was-deleted",
      default_model_id: null,
    };
    renderPicker();
    const { keys, defaultKey } = await lastResolved();
    expect(keys).toContain(defaultKey);
    expect(defaultKey).not.toBe("offering-that-was-deleted");
  });

  it("falls back to the offering flagged as default", async () => {
    optionsPayload = {
      options: [RUNNABLE[0]!, { ...RUNNABLE[1]!, is_default: true }],
      default_offering_id: null,
      default_model_id: null,
    };
    renderPicker();
    await waitFor(() => expect(trigger()).resolves.toHaveTextContent(/azure\/gpt-5-mini/));
  });

  it("falls back to the default model id when no offering id is configured", async () => {
    optionsPayload = {
      options: RUNNABLE,
      default_offering_id: null,
      default_model_id: "azure/gpt-5-mini",
    };
    renderPicker();
    await waitFor(() => expect(trigger()).resolves.toHaveTextContent(/azure\/gpt-5-mini/));
  });

  it("with no default configured at all, preselects a runnable option rather than nothing", async () => {
    // `default_offering_id` and `default_model_id` are both null on the live project.
    //
    // Leaving the picker empty does NOT make the choice explicit: the cockpit sends
    // `offering_id: null, model_id: null` and the BACKEND then picks the organisation
    // default — a model this project may not even be granted, and one this control is
    // not showing. A visible, runnable, deterministic pick beats an invisible one.
    renderPicker();
    const { keys, defaultKey } = await lastResolved();
    expect(defaultKey).toBe(keys[0]);
    expect(await trigger()).toHaveTextContent(/claude-opus-4-5/);
  });

  it("keeps an explicit choice that is still runnable", async () => {
    renderPicker({ value: "azure::azure/gpt-5-mini::prov-azure-key" });
    await waitFor(() => expect(trigger()).resolves.toHaveTextContent(/azure\/gpt-5-mini/));
  });

  it("drops an explicit choice the project can no longer run", async () => {
    // What a project switch leaves behind: the previously chosen key survives in the
    // session while the option set underneath it changes.
    renderPicker({ value: "ai21::jamba-1.5-mini::" });
    const el = await trigger();
    expect(el).not.toHaveTextContent(/jamba/);
    expect(el).toHaveTextContent(/claude-opus-4-5/);
  });
});

void React;
