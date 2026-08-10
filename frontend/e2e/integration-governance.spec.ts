import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * Longer waits than the 5s default — not weaker assertions. `next dev`
 * compiles route segments on first request; every assertion below is
 * unchanged, they are simply allowed to wait for a build.
 */
const expect = baseExpect.configure({ timeout: 20_000 });
test.describe.configure({ timeout: 120_000 });

/**
 * The integration cascade after consumption moved to projects.
 *
 * The rules being asserted are the ones no unit test reaches, because they
 * live in the pages: that the governing tiers see an estate they cannot edit
 * all of, and that a project has a screen of its own for the part it does own.
 */
/**
 * The mock panel is a radio group plus ONE submit button, whose label tracks
 * the selected role ("Continue as Developer"). Signing in as anyone but the
 * default means picking the radio first — there is no per-role button.
 *
 * Selected by id, not by accessible name: each option's label wraps the role
 * name AND its one-liner, so the radio's accessible name is the whole
 * sentence and an exact-name match never hits.
 */
async function signIn(page: Page, role: { slug: string; label: string }) {
  await page.goto("/login");
  await page.locator(`#platform-role-${role.slug}`).check();
  await page.getByRole("button", { name: `Continue as ${role.label}` }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { waitUntil: "commit" });
  await page.waitForLoadState("domcontentloaded");
}

const ORG_ADMIN = { slug: "org_admin", label: "Organization Admin" };
const DEVELOPER = { slug: "developer", label: "Developer" };

test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  const page = await browser.newPage();
  await signIn(page, ORG_ADMIN);
  for (const path of ["/integrations", "/projects/payments-api/integrations", "/projects/payments-api"]) {
    await page.goto(path).catch(() => undefined);
    await page.getByRole("heading").first().waitFor({ timeout: 60_000 }).catch(() => undefined);
  }
  await page.close();
});

test("the integrations page is a grid of cards carrying each one's reach", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations");

  // The three summary tiles are gone — they counted the page you are looking at.
  await expect(page.getByText("Needs attention")).toHaveCount(0);

  // One card per connector, each stating how far it reaches. No credential
  // action anywhere: neither admin tier authenticates to a connector.
  const azure = page.getByRole("link", { name: "Azure DevOps", exact: true });
  await expect(azure).toBeVisible();
  await expect(page.getByText(/business units · \d+ projects/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /add credentials/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^connect$/i })).toHaveCount(0);
  // MCP servers are governed identically — no register/edit/probe/delete either.
  await expect(page.getByRole("button", { name: /register mcp server/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^probe$/i })).toHaveCount(0);

  // Teams and SharePoint are in the catalogue.
  await expect(page.getByRole("link", { name: "Microsoft Teams", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "SharePoint", exact: true })).toBeVisible();

  // MCP servers get the same card, in the same grid shape.
  await expect(page.getByRole("link", { name: "Postgres (staging)", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Card scheme sandbox", exact: true })).toBeVisible();

  // No explanatory tagline on a card. "Read issues + write sub-tasks" is
  // useful once and then sits there forever; the reach is what changes.
  await expect(page.getByText("Read issues + write sub-tasks. Webhook-driven.")).toHaveCount(0);
  await expect(page.getByText("Read/write access to the project's repo checkout.")).toHaveCount(0);

  // No org-wide / business-unit scope anywhere. An MCP server has no level of
  // its own any more — the grant is the whole answer, same as a connector.
  await expect(page.getByText("org-wide")).toHaveCount(0);
  await expect(page.getByText("business unit", { exact: true })).toHaveCount(0);
  // And no transport badges: they described a connection nobody configures here.
  await expect(page.getByText("streamable_http")).toHaveCount(0);
  await expect(page.getByText("stdio")).toHaveCount(0);

  // The count is units that HOLD it, not every unit that could be given it.
  // Slack reaches Payments alone; before this it read "3 business units".
  // Located by the card's own link — the taglines they used to be found by are
  // gone, and a card is identified by what it IS, not by prose about it.
  const slack = page.locator("li").filter({ has: page.getByRole("link", { name: "Slack", exact: true }) });
  await expect(slack.getByText(/^1 business unit · /)).toBeVisible();
  const cardScheme = page
    .locator("li")
    .filter({ has: page.getByRole("link", { name: "Card scheme sandbox", exact: true }) });
  await expect(cardScheme.getByText("1 business unit · 1 project")).toBeVisible();

  await azure.click();
  await expect(page).toHaveURL(/\/integrations\/azure_devops$/);
});

test("a connector's screen names the units holding it, and expands to projects", async ({
  page,
}) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/jira");
  await expect(page.getByRole("heading", { name: /Jira/ }).first()).toBeVisible();

  // Units first — the question that brings you here. Projects stay collapsed.
  await expect(page.getByText("Lending", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Core ledger — Java 8 to 21" })).toHaveCount(0);

  await page.getByRole("button", { name: /Show projects in Lending using/ }).click();
  await expect(page.getByRole("link", { name: "Core ledger — Java 8 to 21" })).toBeVisible();

  // And no credentials anywhere on the screen.
  await expect(page.getByText("Connections")).toHaveCount(0);
  await expect(page.getByText(/Where the credentials/)).toHaveCount(0);
});

test("a business unit can be granted access after it was created", async ({ page }) => {
  // The gap this closes: the screen listed who held the integration and gave
  // no way to add anyone, so access could be taken away and never given back.
  await signIn(page, ORG_ADMIN);
  // Azure DevOps reaches Lending and Payments; Platform is offered as a grant.
  // Deliberately NOT a unit-owned connector like Slack or GitHub Actions — one
  // a unit onboarded itself reaches only that unit by construction, so there is
  // correctly nobody to grant it to.
  await page.goto("/integrations/azure_devops");

  // The control is always present, and searchable — it does not appear only
  // when something happens to be ungranted.
  await page.getByRole("button", { name: /Grant to a business unit/i }).click();
  await page.getByRole("option", { name: /Platform Engineering/ }).click();

  await expect(page.getByText(/Platform Engineering can now use/)).toBeVisible();
  // It moves out of the "not granted" strip and into the held list.
  await expect(
    page.getByRole("button", { name: /Show projects in Platform Engineering using/ }),
  ).toBeVisible();
});

test("every integration can be granted to any unit, including a narrow one", async ({ page }) => {
  // Slack reaches Payments only. It used to be flagged "onboarded here" and so
  // ungrantable to anyone else — a pin that made sense only while a unit held
  // its own credential for it, which nobody does now.
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/slack");

  await expect(page.getByText("Payments", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Grant to a business unit/i }).click();
  await page.getByRole("option", { name: /Lending/ }).click();

  await expect(page.getByText(/Lending can now use/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Show projects in Lending using/ })).toBeVisible();
});

test("an MCP server can be added, and reaches nobody until granted", async ({ page }) => {
  // Connectors need no equivalent — their kinds ship in the catalogue. An MCP
  // server is whatever someone stood up, so without this the estate could only
  // ever shrink.
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations");

  await page.getByRole("button", { name: /add mcp server/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("Snowflake (analytics)");
  await dialog.getByLabel("URL").fill("https://mcp-snowflake.internal.acme.test");
  await dialog.getByRole("button", { name: /^add server$/i }).click();

  await expect(page.getByText(/Snowflake \(analytics\) registered/)).toBeVisible();
  // Registering is not granting: it appears with no unit behind it.
  const card = page
    .locator("li")
    .filter({ has: page.getByRole("link", { name: "Snowflake (analytics)", exact: true }) });
  await expect(card.getByText("0 business units · 0 projects")).toBeVisible();
});

test("the crumb names an integration the way its heading does", async ({ page }) => {
  // `mcp_filesystem` title-cased into "Mcp_filesystem" beside a heading
  // reading "Filesystem" — the crumb and the page disagreeing about the name
  // of the thing on screen.
  await signIn(page, ORG_ADMIN);
  const crumbs = page.getByRole("navigation", { name: /breadcrumb/i });

  await page.goto("/integrations/mcp_filesystem");
  await expect(crumbs.getByText("Filesystem", { exact: true })).toBeVisible();
  await expect(crumbs.getByText("Mcp_filesystem", { exact: true })).toHaveCount(0);

  await page.goto("/integrations/jira");
  await expect(crumbs.getByText("Jira Cloud — Acme", { exact: true })).toBeVisible();
});

test("an MCP server can be granted to a unit that lacks it", async ({ page }) => {
  // The MCP half of the same rule — the card-scheme server was pinned to
  // Payments for exactly the same reason.
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/mcp_card_scheme");

  await page.getByRole("button", { name: /Grant to a business unit/i }).click();
  await page.getByRole("option", { name: /Platform Engineering/ }).click();
  await expect(page.getByText(/Platform Engineering can now use/)).toBeVisible();
});

test("an MCP server gets the same screen and the same rules", async ({ page }) => {
  // Governed identically, so one route serves both — the id's `mcp_` prefix is
  // what tells them apart.
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/mcp_postgres");

  await expect(page.getByRole("heading", { name: "Postgres (staging)" })).toBeVisible();
  await expect(page.getByText(/MCP server · which business units/)).toBeVisible();

  // Same as connectors: nothing here registers, edits, probes or deletes.
  await expect(page.getByRole("button", { name: /register mcp server/i })).toHaveCount(0);

  // Units, expandable to projects, revocable — the connector screen's contract.
  await expect(page.getByText("Lending", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Show projects in Lending using/ }).click();
  await expect(page.getByRole("button", { name: /^Remove Postgres .* from Lending$/ })).toBeVisible();

  // And no credentials, same as connectors.
  await expect(page.getByText("Connections")).toHaveCount(0);
});

test("revoking on a connector's screen asks for a second click", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/jira");

  const revoke = page.getByRole("button", { name: /^Remove Jira .* from Lending$/ }).first();
  await expect(revoke).toBeVisible();

  // First click arms; it does not fire.
  await revoke.click();
  await expect(page.getByRole("button", { name: /^Confirm: Remove Jira .* from Lending$/ })).toBeVisible();
  await expect(page.getByText("Lending", { exact: true })).toBeVisible();
});

test("a project can be dropped from a connector without touching the unit's grant", async ({
  page,
}) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/integrations/jira");
  await page.getByRole("button", { name: /Show projects in Lending using/ }).click();

  // Arm, then confirm. The button renames itself when armed, so the second
  // click has to target the confirm state — clicking the same locator twice
  // would just re-arm it.
  await page.getByRole("button", { name: /^Remove Jira .* from Core ledger/ }).click();
  await page.getByRole("button", { name: /^Confirm: Remove Jira .* from Core ledger/ }).click();

  // The project goes; Lending keeps the grant — the two levels are separate.
  await expect(page.getByRole("link", { name: "Core ledger — Java 8 to 21" })).toHaveCount(0);
  await expect(page.getByText("Lending", { exact: true })).toBeVisible();
});

test("a project's Integrations screen lists what it may use, and its credentials", async ({
  page,
}) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/projects/payments-api/integrations");
  await expect(page.getByRole("heading", { name: "Integrations", exact: true })).toBeVisible();

  // Wired to a stage by the Project Admin, inherited from above.
  await expect(page.getByText("Jira Cloud — Acme")).toBeVisible();
  // Payments' own MCP server — a unit-scoped thing reaching its own project.
  await expect(page.getByText("Card scheme sandbox")).toBeVisible();

  // The project's own half: a credential it holds, and one it still owes.
  await expect(page.getByText("svc-payments@acme.test")).toBeVisible();
  await expect(page.getByText("Needs a credential").first()).toBeVisible();
});

test("the project screen does not offer to change which integrations it has", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/projects/payments-api/integrations");
  await expect(page.getByText("Jira Cloud — Acme")).toBeVisible();

  // Choosing is done above and in Settings → Tools per stage. Offering it here
  // would be a second place for the same decision to disagree with itself.
  await expect(page.getByRole("button", { name: /^connect$/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /add provider|onboard/i })).toHaveCount(0);
});

test("a contributor requests agent access from Overview, with a justification", async ({
  page,
}) => {
  await signIn(page, DEVELOPER);
  await page.goto("/projects/payments-api");

  await page.getByRole("button", { name: /request agent access/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // Both approvers are named before sending — the second one is not a surprise.
  await dialog.getByRole("combobox").click();
  await page.getByRole("option").first().click();
  // The specific line, not the description's passing mention of the phrase.
  await expect(dialog.getByText(/^Final sign-off: /)).toBeVisible();

  // The justification is required: an empty ask is the whole of what two
  // people are being asked to decide on.
  const send = dialog.getByRole("button", { name: /send request/i });
  await expect(send).toBeDisabled();

  await dialog
    .getByLabel(/why you need it/i)
    .fill("Covering the BA this sprint — I need to update acceptance criteria before the gate.");
  await expect(send).toBeEnabled();
  await send.click();

  await expect(page.getByText(/sent to your project admin/i)).toBeVisible();
});

test("the Organization Admin is not offered an agent-access request", async ({ page }) => {
  // They hold no agent access by design, and the chain ends at them — offering
  // the request would suggest that decision is negotiable.
  await signIn(page, ORG_ADMIN);
  await page.goto("/projects/payments-api");
  await expect(page.getByRole("heading", { name: /payments api/i }).first()).toBeVisible();

  await expect(page.getByRole("button", { name: /request agent access/i })).toHaveCount(0);
});

test("a project's Integrations tab is reachable from the project nav", async ({ page }) => {
  await signIn(page, ORG_ADMIN);
  await page.goto("/projects/payments-api");

  const nav = page.getByRole("navigation", { name: /project sections/i });
  await expect(nav.getByRole("link", { name: "Integrations" })).toBeVisible();
  await nav.getByRole("link", { name: "Integrations" }).click();
  await expect(page).toHaveURL(/\/projects\/payments-api\/integrations$/);
});
