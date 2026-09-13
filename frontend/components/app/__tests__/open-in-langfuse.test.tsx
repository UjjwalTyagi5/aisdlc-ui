import { describe, expect, it } from "vitest";

import { pickLangfuseControl } from "@/components/app/open-in-langfuse";
import { LangfuseLinkList } from "@/lib/schemas/trace";

/**
 * Who is offered a way out into Langfuse.
 *
 * THE RULE THIS PROTECTS. The control is driven by the viewer's REAL Langfuse grants,
 * fetched from the server, not by the `trace:view` permission that gates the Traces page.
 * The two agree today — the four roles holding that permission are exactly the four that
 * receive a Langfuse grant — so a permission-gated button would be right by coincidence.
 * It stops being right the moment either list changes, and the failure mode is a link
 * that drops somebody on an access-denied page: the product reads as broken rather than
 * as correctly restrictive.
 *
 * "none" is the case worth pinning hardest, because it renders nothing and so regresses
 * silently — the button appearing for someone who cannot follow it looks like a feature
 * working, right up until they click it.
 *
 * Asserted as logic rather than markup: this repo runs vitest in the node environment
 * throughout, and the question here is who gets a link, not what the button looks like.
 */
const link = (name: string, over: Record<string, unknown> = {}) =>
  ({
    projectId: `p-${name}`,
    projectName: name,
    url: `https://lf.test/project/lf-${name}/traces`,
    access: "member",
    role: "MEMBER",
    ...over,
  }) as never;

const settled = { loading: false, error: false };

describe("pickLangfuseControl", () => {
  it("offers nothing to somebody with no Langfuse grant", () => {
    expect(pickLangfuseControl([], settled)).toEqual({ kind: "none" });
  });

  it("offers nothing while the answer is still unknown", () => {
    // Unknown must never render as allowed — see the component docstring.
    expect(pickLangfuseControl([link("A")], { loading: true, error: false }))
      .toEqual({ kind: "none" });
  });

  it("offers nothing when the lookup failed", () => {
    // "We could not tell whether you have access" is not "you have access".
    expect(pickLangfuseControl([link("A")], { loading: false, error: true }))
      .toEqual({ kind: "none" });
  });

  it("links straight through when exactly one project is reachable", () => {
    expect(pickLangfuseControl([link("Checkout")], settled)).toEqual({
      kind: "single",
      href: "https://lf.test/project/lf-Checkout/traces",
    });
  });

  it("offers a choice when several are reachable", () => {
    expect(pickLangfuseControl([link("A"), link("B")], settled))
      .toEqual({ kind: "menu" });
  });
});

describe("the LangfuseLink contract", () => {
  it("accepts a pending invitation as a linkable state", () => {
    // Following the link is exactly what converts the invitation, so it must survive
    // parsing rather than being filtered out as "not access yet".
    const parsed = LangfuseLinkList.parse([link("A", { access: "invited" })]);
    expect(parsed[0]!.access).toBe("invited");
  });

  it("refuses a link the server should never have sent", () => {
    // The backend drops anyone resolving to no access. If one ever leaked through, the
    // schema is the second place it dies — before it can be rendered as a working link.
    expect(() => LangfuseLinkList.parse([link("A", { access: "none" })])).toThrow();
  });

  it("requires a url", () => {
    expect(() => LangfuseLinkList.parse([link("A", { url: undefined })])).toThrow();
  });
});
