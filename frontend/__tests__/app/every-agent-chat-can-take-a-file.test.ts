/**
 * Every agent chat can be given a file, and the file has somewhere to live.
 *
 * TWO HALVES, and each was broken on a different set of pages.
 *
 *  · `onAttachFiles` is what makes the paperclip appear at all — the drawer renders it
 *    disabled without the callback. Code Review, Security and Documentation had chats
 *    with no way to attach anything, while five other agents did.
 *  · `projectId` is what turns on the persisted session. Attachments are stored under
 *    `files/{user}/attachments/{session}/`, so a chat without one has nowhere to put a
 *    file and `attachFiles` used to return without a word.
 *
 * A page can satisfy either half alone and still be broken, which is why both are
 * asserted here rather than left to whoever adds the next agent page.
 *
 * The backend half — that the module serving the agent's socket actually READS what
 * was attached — is `backend/tests/test_every_agent_chat_reads_attachments.py`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const PROJECT_PAGES = join(__dirname, "..", "..", "app", "(app)", "projects", "[id]");

/** Every page under a project that mounts the agent chat drawer. */
function pagesWithAChat(): { name: string; src: string }[] {
  return readdirSync(PROJECT_PAGES)
    .filter((entry) => statSync(join(PROJECT_PAGES, entry)).isDirectory())
    .map((entry) => ({ name: entry, file: join(PROJECT_PAGES, entry, "page.tsx") }))
    .filter(({ file }) => {
      try {
        return readFileSync(file, "utf8").includes("<AgentChatDrawer");
      } catch {
        return false;
      }
    })
    .map(({ name, file }) => ({ name, src: readFileSync(file, "utf8") }));
}

describe("agent chats accept attachments", () => {
  it("finds the agent pages at all", () => {
    // A path that stopped resolving would make every assertion below vacuous.
    expect(pagesWithAChat().length).toBeGreaterThanOrEqual(8);
  });

  it("every chat offers the attach control", () => {
    const missing = pagesWithAChat()
      .filter((p) => !p.src.includes("onAttachFiles="))
      .map((p) => p.name);

    expect(missing, "pages whose chat cannot take a file").toEqual([]);
  });

  it("every chat has a session to store the file against", () => {
    const missing = pagesWithAChat()
      // `projectId,` (shorthand) and `projectId: id` are both how the pages write it.
      .filter((p) => !/useAgentChat\(\{[\s\S]{0,600}?projectId\s*[,:]/.test(p.src))
      .map((p) => p.name);

    expect(missing, "pages whose chat has no persisted session").toEqual([]);
  });
});
