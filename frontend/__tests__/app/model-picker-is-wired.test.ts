/**
 * A model picker that is wired to nothing looks exactly like one that works.
 *
 * That is the whole defect: `agentModel` was set by the ModelSelector on every
 * standalone agent page and read by nothing. The dropdown opened, the choice stuck,
 * the label updated — and the value went nowhere. Every turn resolved with no model
 * and fell through to whichever provider connection sorts first by display name.
 *
 * The hook and the agent nodes are covered by behavioural tests. The two links
 * between them are not reachable from a unit test — a page is a server-rendered
 * React tree and `openChatWsBridge` is module-private and opens a real WebSocket —
 * so they are pinned at the source instead. A source check is weak evidence about
 * behaviour and strong evidence about *presence*, and presence is exactly what went
 * missing here.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

describe("the chat BFF forwards the chosen offering", () => {
  const route = read("app/api/chat/route.ts");

  it("puts offering_id on the agent WS payload", () => {
    expect(route).toContain("offering_id: offeringId");
  });

  it("omits it when the page made no choice, so the org default still applies", () => {
    // Sending `offering_id: undefined` is not the same thing — JSON.stringify drops
    // it, but a null would reach the agent and there is no reason to rely on that.
    expect(route).toContain("offeringId ? { offering_id: offeringId } : {}");
  });

  it("reads it off the request body rather than inventing one", () => {
    expect(route).toMatch(/const \{[^}]*offeringId[^}]*\} = body;/);
  });
});

describe("every surface with a model picker sends what it picked", () => {
  //: The two files that own a ModelSelector AND a chat. `stage-workbench` is the
  //: shared header for seven stages, so it counts for all of them.
  const SURFACES = [
    "app/(app)/projects/[id]/deployment/page.tsx",
    "app/(app)/projects/[id]/design/page.tsx",
    "app/(app)/projects/[id]/development/page.tsx",
    "app/(app)/projects/[id]/requirements/page.tsx",
    "app/(app)/projects/[id]/testing/page.tsx",
    "components/app/stage-workbench.tsx",
    // Track 3's shared agent shell — both of its first two agents' pages.
    "components/modernization/track3-agent-page.tsx",
  ];

  it.each(SURFACES)("%s hands its picker value to useAgentChat", (path) => {
    const src = read(path);
    expect(src).toContain("<ModelSelector");
    expect(src).toContain("offeringId: agentModel");
  });

  it("finds every ModelSelector-with-chat surface, so the list above is not stale", () => {
    // Guards the guard. If a new agent page grows a picker and a chat, this fails
    // rather than letting the audit quietly cover less than it claims to.
    // `rel` is built with "/" from the start, so there is nothing to normalise.
    const found: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(join(ROOT, dir), { withFileTypes: true })) {
        const rel = `${dir}/${e.name}`;
        if (e.isDirectory()) {
          if (e.name === "node_modules" || e.name === "__tests__" || e.name === ".next") continue;
          walk(rel);
        } else if (e.name.endsWith(".tsx")) {
          const src = readFileSync(join(ROOT, rel), "utf8");
          if (src.includes("<ModelSelector") && src.includes("useAgentChat(")) {
            found.push(rel);
          }
        }
      }
    };
    walk("app");
    walk("components");

    expect(found.sort()).toEqual([...SURFACES].sort());
  });
});
