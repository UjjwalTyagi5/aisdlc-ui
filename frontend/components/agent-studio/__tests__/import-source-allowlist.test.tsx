// @vitest-environment jsdom
/**
 * Org Admin allowlist management UI (Agent Studio import + supply-chain
 * screening, Task 7). Mirrors the backend's read/write split (Task 4):
 * `GET /agent-skills/import-sources` is open to any tenant member,
 * `POST /agent-skills/import-sources` is Org-Admin-only.
 *
 * Follows the mocking convention from
 * components/agent-studio/__tests__/skills-tab.test.tsx: this repo has no
 * wired MSW server for component tests, so `@/lib/api/agent-skills` is
 * mocked directly with vi.mock, `@/components/auth/session-provider` is
 * mocked to control `effectivePlatformRole`'s input, and (per
 * __tests__/app/provider-detail-rbac-gate.test.tsx, needed here because
 * ImportSourceAllowlist uses useQuery/useMutation) the component is wrapped
 * in a fresh QueryClientProvider.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/agent-skills", () => ({
  listImportSources: vi.fn(),
  createImportSource: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import type { Session } from "@/lib/auth/types";

// Signed-in viewer's session — mocked per-test, matching skills-tab.test.tsx's
// convention (session-provider isn't wired in component tests). Drives
// `effectivePlatformRole()`, which is what gates the add form.
let mockSession: Session | null = null;
vi.mock("@/components/auth/session-provider", () => ({
  useRawSession: () => mockSession,
}));

import { createImportSource, listImportSources } from "@/lib/api/agent-skills";

import { ImportSourceAllowlist } from "../import-source-allowlist";

afterEach(() => {
  cleanup();
  mockSession = null;
  vi.clearAllMocks();
});

const mockedList = vi.mocked(listImportSources);
const mockedCreate = vi.mocked(createImportSource);

function orgAdminSession(): Session {
  return {
    user: { id: "admin-1", name: "Org Admin", email: "admin@acme.test", initials: "OA" },
    tenant: { id: "tenant-1", name: "Acme", plan: "enterprise" },
    role: "admin",
    mode: "mock",
    permissions: ["admin:*"],
  };
}

function developerSession(): Session {
  return {
    user: { id: "dev-1", name: "A Developer", email: "dev@acme.test", initials: "AD" },
    tenant: { id: "tenant-1", name: "Acme", plan: "enterprise" },
    role: "member",
    mode: "mock",
    permissions: ["artifact:view", "run:create"],
  };
}

function renderComponent() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportSourceAllowlist />
    </QueryClientProvider>,
  );
}

describe("ImportSourceAllowlist", () => {
  it("lets an Org Admin add an allowlist entry and shows it in the list", async () => {
    mockSession = orgAdminSession();
    mockedList.mockResolvedValue({ sources: [] });
    mockedCreate.mockResolvedValue({
      id: "src-1",
      source_pattern: "https://github.com/acme-org/",
      label: "Acme",
    });

    const user = userEvent.setup();
    renderComponent();

    await user.type(await screen.findByLabelText(/label/i), "Acme");
    await user.type(screen.getByLabelText(/pattern|url/i), "https://github.com/acme-org/");
    await user.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith({
        source_pattern: "https://github.com/acme-org/",
        label: "Acme",
      }),
    );
  });

  it("invalidates the list so the new entry appears after a successful add", async () => {
    mockSession = orgAdminSession();
    mockedList
      .mockResolvedValueOnce({ sources: [] })
      .mockResolvedValueOnce({
        sources: [
          {
            id: "src-1",
            source_pattern: "https://github.com/acme-org/",
            label: "Acme",
            created_by: "admin@acme.test",
            created_at: "2026-08-01T00:00:00Z",
          },
        ],
      });
    mockedCreate.mockResolvedValue({
      id: "src-1",
      source_pattern: "https://github.com/acme-org/",
      label: "Acme",
    });

    const user = userEvent.setup();
    renderComponent();

    await user.type(await screen.findByLabelText(/label/i), "Acme");
    await user.type(screen.getByLabelText(/pattern|url/i), "https://github.com/acme-org/");
    await user.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("https://github.com/acme-org/")).toBeInTheDocument();
  });

  it("hides the add form from a non Org Admin, but still renders the list", async () => {
    mockSession = developerSession();
    mockedList.mockResolvedValue({
      sources: [
        {
          id: "src-1",
          source_pattern: "https://github.com/acme-org/",
          label: "Acme",
          created_by: "admin@acme.test",
          created_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    renderComponent();

    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.queryByLabelText(/label/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add/i })).not.toBeInTheDocument();
  });

  it("hides the add form when there is no session at all", async () => {
    mockSession = null;
    mockedList.mockResolvedValue({ sources: [] });

    renderComponent();

    expect(await screen.findByText(/no import sources yet/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/label/i)).not.toBeInTheDocument();
  });

  it("renders an empty state when there are no entries yet", async () => {
    mockSession = orgAdminSession();
    mockedList.mockResolvedValue({ sources: [] });

    renderComponent();

    expect(await screen.findByText(/no import sources yet/i)).toBeInTheDocument();
  });
});
