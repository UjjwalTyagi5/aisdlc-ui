// @vitest-environment jsdom
/**
 * The model picker must survive its own loading state.
 *
 * THE CRASH. Every agent page and the project overview render this control, and
 * every one of them went to the error screen on a fresh load. The picker reported
 * the default it displays through a `useRef` + `useEffect` placed AFTER its
 * "Loading models…" early return: the loading render ran N hooks, the loaded render
 * ran N + 2, and React threw "Rendered more hooks than during the previous render".
 * With the options already cached (60s) there was no loading render — which is why
 * the same page crashed on one visit and worked on the next.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const OPTIONS = {
  options: [
    { offering_id: "off-xai", provider_id: "p1", display_name: "xAI", provider: "xai", model_id: "grok-3-mini", is_default: true },
    { offering_id: "off-az", provider_id: "p2", display_name: "Azure", provider: "azure_openai", model_id: "gpt-5-mini", is_default: false },
  ],
  default_offering_id: "off-xai",
  default_model_id: "grok-3-mini",
};

let resolveOptions: (v: typeof OPTIONS) => void = () => {};
vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(() => new Promise((r) => { resolveOptions = r; })),
}));

import { ModelSelector } from "@/components/app/model-selector";

afterEach(cleanup);

function renderPicker(onValueChange: (v: string | undefined) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelSelector projectId="proj-1" onValueChange={onValueChange} />
    </QueryClientProvider>,
  );
}

describe("ModelSelector", () => {
  it("goes from loading to loaded without breaking the rules of hooks, and reports the default it shows", async () => {
    const onValueChange = vi.fn();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    renderPicker(onValueChange);
    expect(screen.getByText("Loading models…")).toBeInTheDocument();

    resolveOptions(OPTIONS);

    await waitFor(() => expect(onValueChange).toHaveBeenCalledWith("off-xai"));
    expect(screen.queryByText("Loading models…")).not.toBeInTheDocument();
    expect(onValueChange).toHaveBeenCalledTimes(1);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/more hooks|order of Hooks/i);
    errors.mockRestore();
  });
});
