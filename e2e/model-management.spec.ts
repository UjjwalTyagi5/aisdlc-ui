import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * Longer waits than the 5s default — not weaker assertions.
 *
 * `next dev` compiles route segments on first request, and on a slow disk a
 * page can still be building when a default-timeout assertion gives up. The
 * failure then reads as missing UI. Every assertion below is unchanged; they
 * are simply allowed to wait for a build.
 */
const expect = baseExpect.configure({ timeout: 20_000 });
test.describe.configure({ timeout: 120_000 });

/**
 * The Org Admin's model screens: what is onboarded, and who may use it.
 *
 * These cover the two filters and the keyless-credential copy — logic that
 * lives inline in the pages and so has no unit test. The states being asserted
 * (a provider registered without a key, one model on two subscriptions) are
 * seeded in `lib/mock/model-fixtures.ts`; if a fixture stops representing them,
 * these fail here rather than being noticed on a screenshot months later.
 */

/** The mock panel's platform-role picker defaults to Organization Admin, which
 *  is the role these screens belong to — so signing in is a single submit. */
async function signInAsOrgAdmin(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /continue as organization admin/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { waitUntil: "commit" });
  await page.waitForLoadState("domcontentloaded");
}

/**
 * Warm the routes before asserting on them.
 *
 * `next dev` compiles a route segment the first time it is requested, and on a
 * cold `.next` that takes longer than an assertion is willing to wait — the
 * page under test renders nothing and the failure looks like missing UI rather
 * than a build still running. One throwaway request per route pays that cost
 * once, outside the tests.
 */
test.beforeAll(async ({ browser }) => {
  // Compiling four route segments costs more than a test's default budget, and
  // this hook is paying it for all of them.
  test.setTimeout(240_000);
  // Signed in, because these routes are gated: an anonymous request redirects
  // to /login and compiles nothing, which is a warm-up that warms nothing.
  const page = await browser.newPage();
  await signInAsOrgAdmin(page);
  for (const path of ["/admin/models", "/admin/models/anthropic", "/admin/models/openai", "/admin/models/bedrock"]) {
    await page.goto(path).catch(() => undefined);
    await page.getByRole("heading").first().waitFor({ timeout: 60_000 }).catch(() => undefined);
  }
  await page.close();
});

test.beforeEach(async ({ page }) => {
  await signInAsOrgAdmin(page);
});

test("the list names every onboarded provider, and search filters by model", async ({ page }) => {
  await page.goto("/admin/models");
  await expect(page.getByRole("heading", { name: "Models", exact: true })).toBeVisible();

  for (const name of [
    "Anthropic",
    "OpenAI",
    "Azure OpenAI",
    "AWS Bedrock",
    "Google Vertex AI",
    "xAI (Grok)",
  ]) {
    await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
  }

  // Searching a MODEL, not a vendor — the question a vendor-name filter cannot
  // answer, and the reason the filter reaches inside the cards.
  const search = page.getByLabel("Search providers, subscriptions or models");
  await search.fill("grok");
  await expect(page.getByText("xAI (Grok)", { exact: true })).toBeVisible();
  await expect(page.getByText("AWS Bedrock", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/1 of \d+ providers/)).toBeVisible();

  await search.fill("nothing-matches-this");
  await expect(page.getByText(/No provider, subscription or model matches/)).toBeVisible();
});

test("the access table filters by credential, not only by model", async ({ page }) => {
  await page.goto("/admin/models/anthropic");
  await expect(page.getByRole("heading", { name: "Anthropic" })).toBeVisible();
  await expect(page.getByText("claude-opus-4-7")).toBeVisible();

  // Sonnet is served by two subscriptions; naming one must narrow to its row.
  await page.getByLabel(/Search models, credentials or/).fill("EU gateway");
  await expect(page.getByText("claude-opus-4-7")).toHaveCount(0);
  await expect(page.getByText("Anthropic — EU gateway").last()).toBeVisible();
  await expect(page.getByText("1 of 4")).toBeVisible();
});

test("a subscription holding no key says so instead of reading as ready", async ({ page }) => {
  await page.goto("/admin/models/bedrock");
  await expect(page.getByRole("heading", { name: "AWS Bedrock" })).toBeVisible();

  // Granted to everyone AND unusable — both halves have to be legible, and
  // both now have to come from the table, since the Credentials card is gone.
  await expect(page.getByText("All business units").first()).toBeVisible();
  await expect(page.getByText("Holds no key").first()).toBeVisible();

  // A provider whose keys are real must not pick up the warning.
  await page.goto("/admin/models/anthropic");
  await expect(page.getByText(/Holds no key/)).toHaveCount(0);
});

test("the credential column carries scope and status, the removed card's facts", async ({
  page,
}) => {
  // OpenAI's only key belongs to Lending — the whole reason Payments is granted
  // gpt-5.1 and still cannot run it. That fact used to live in a card of its own.
  await page.goto("/admin/models/openai");
  await expect(page.getByText("OpenAI — Lending trial").first()).toBeVisible();
  await expect(page.getByText(/Lending only/i).first()).toBeVisible();

  // And there is no second list restating the same subscription names.
  await expect(page.getByRole("heading", { name: "Credentials" })).toHaveCount(0);
});

test("onboarding discloses step by step, and demands Azure's endpoint", async ({ page }) => {
  await page.goto("/admin/models");
  await page.getByRole("button", { name: "Add provider" }).click();

  // The key is not asked for before the models it would serve are chosen.
  await expect(page.getByLabel("Subscription name")).toHaveCount(0);

  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: /Azure OpenAI/ }).click();
  await expect(page.getByLabel("Subscription name")).toHaveCount(0);

  await page.getByRole("checkbox").first().check();
  await expect(page.getByLabel("Subscription name")).toBeVisible();
  await expect(page.getByText(/Azure has no shared endpoint/)).toBeVisible();
  // Limits stay collapsed — the one genuinely skippable section.
  await expect(page.getByRole("button", { name: /Set RPM, TPM or a monthly cap/ })).toBeVisible();
});

test("the breadcrumb names the provider the way its heading does", async ({ page }) => {
  // The URL segment is a routing slug; title-casing it produced "Openai" beside
  // a heading reading "OpenAI" — the crumb and the page disagreeing about the
  // name of the thing on screen.
  // Scoped to the crumb trail: "Model Management" also names a sidebar link.
  const crumbs = page.getByRole("navigation", { name: /breadcrumb/i });

  await page.goto("/admin/models/openai");
  await expect(crumbs.getByText("OpenAI", { exact: true })).toBeVisible();
  await expect(crumbs.getByText("Openai", { exact: true })).toHaveCount(0);

  await page.goto("/admin/models/bedrock");
  await expect(crumbs.getByText("AWS Bedrock", { exact: true })).toBeVisible();
  await expect(crumbs.getByText("Bedrock", { exact: true })).toHaveCount(0);
});

test("the crumb trail carries no routing-only ancestor", async ({ page }) => {
  // /admin has no page — the crumb linked to a 404, and named a section the
  // sidebar does not have (Model Management sits under GOVERN).
  const crumbs = page.getByRole("navigation", { name: /breadcrumb/i });

  await page.goto("/admin/models");
  await expect(crumbs.getByText("Model Management")).toBeVisible();
  await expect(crumbs.getByText("Administration")).toHaveCount(0);

  // The real ancestor survives one level deeper — dropping a prefix must not
  // drop the parent, and its link must still point at the parent's own path.
  await page.goto("/admin/models/openai");
  await expect(crumbs.getByRole("link", { name: "Model Management" })).toHaveAttribute(
    "href",
    "/admin/models",
  );
});

test("the summary row states the estate, and does not move when you filter", async ({ page }) => {
  await page.goto("/admin/models");
  // The tile's own label element, not "any div whose text is X" — the page's
  // name is now an sr-only <h1>, and a loose div filter matched its wrapper
  // and then failed on it being hidden.
  const tile = (label: string) =>
    page.locator("span").filter({ hasText: new RegExp(`^${label}$`) }).first();

  await expect(tile("Providers")).toBeVisible();
  await expect(tile("Models")).toBeVisible();
  await expect(tile("Org-wide")).toBeVisible();
  // Size only. Health belongs against the subscription it affects — the status
  // pill on the card and "Holds no key" on the provider screen.
  await expect(page.getByText("Needs a key")).toHaveCount(0);

  // The counts describe everything, not the filtered view. A total that moved
  // as you typed would read as a filtered total.
  const before = await page.getByText("Providers", { exact: true }).locator("..").innerText();
  await page.getByLabel("Search providers, subscriptions or models").fill("grok");
  await expect(page.getByText(/1 of \d+ providers/)).toBeVisible();
  expect(await page.getByText("Providers", { exact: true }).locator("..").innerText()).toBe(before);
});

test("the provider card is the drill-in, and carries no key actions", async ({ page }) => {
  await page.goto("/admin/models");
  await expect(page.getByRole("heading", { name: "Models", exact: true })).toBeVisible();

  // Edit / Remove act on ONE key and moved to the provider's screen, which is
  // the only place that also shows what each key serves.
  await expect(page.getByRole("button", { name: /^Edit / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Remove / })).toHaveCount(0);
  // And the separate drill-in link is gone — the card itself is the link.
  await expect(page.getByRole("link", { name: /Models & access/ })).toHaveCount(0);

  await page.getByRole("link", { name: "Anthropic", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/models\/anthropic$/);
});

test("choosing the default model does not navigate away", async ({ page }) => {
  // The card is a stretched link; the radios sit above it deliberately. If the
  // overlay swallowed the click, picking a default would silently drill in.
  await page.goto("/admin/models");
  const radio = page.getByRole("radio").first();
  await radio.check();

  await expect(page).toHaveURL(/\/admin\/models$/);
  await expect(radio).toBeChecked();
});

test("a subscription's key, endpoint and limits are all editable from its provider", async ({
  page,
}) => {
  // The whole credential is edited in one place. Splitting "rotate the key"
  // from "change the endpoint" from "set a cap" would be three screens for one
  // decision — an EU gateway IS a key, a base URL and its own rate ceiling.
  await page.goto("/admin/models/anthropic");
  await page.getByRole("button", { name: /^Edit / }).first().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Display name")).toBeVisible();
  // Never echoed back — the field offers rotation, it does not show the secret.
  await expect(dialog.getByLabel(/API key/i)).toHaveValue("");
  await expect(dialog.getByPlaceholder(/rotate/i)).toBeVisible();
  await expect(dialog.getByLabel(/API base|endpoint/i)).toBeVisible();
  await expect(dialog.getByPlaceholder("RPM")).toBeVisible();
  await expect(dialog.getByPlaceholder("TPM")).toBeVisible();
  await expect(dialog.getByPlaceholder("$ / month")).toBeVisible();
});

test("an ungranted model can be given access, not only taken away", async ({ page }) => {
  // The hole this closes: the unit picker was disabled while a model was
  // ungranted and the only row action was Revoke, so this screen could take
  // access away and never give it.
  await page.goto("/admin/models/anthropic");
  await expect(page.getByRole("heading", { name: "Anthropic" })).toBeVisible();

  // No Test action anywhere: the row already states the key's standing, and a
  // button that only re-reports what is on screen is a verb with no decision.
  await expect(page.getByRole("button", { name: /^Test / })).toHaveCount(0);

  const grant = page.getByRole("button", { name: "Grant to all" }).first();
  if (await grant.count()) {
    await grant.click();
    await expect(page.getByText("Model access updated")).toBeVisible();
  }

  // Whatever the seeded state, every row offers one of the two verbs — never
  // neither, which is what "disabled picker + no grant action" amounted to.
  const rows = page.locator("tbody tr");
  const first = rows.first();
  await expect(
    first.getByRole("button", { name: /Grant to all|Revoke/ }),
  ).toHaveCount(1);
});

test("removing a subscription names the models it would strand", async ({ page }) => {
  // The reason this action moved here. The list could only ask "remove this
  // key?"; this screen knows what runs on it, so it can say what breaks.
  await page.goto("/admin/models/anthropic");
  await page.getByRole("button", { name: /^Remove / }).first().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/no key behind|also served by another key/i)).toBeVisible();

  // Non-destructive until confirmed.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
});

test("a key can be rotated from the screen that shows what runs on it", async ({ page }) => {
  await page.goto("/admin/models/anthropic");
  await expect(page.getByRole("heading", { name: "Anthropic" })).toBeVisible();

  // The credential name in the table IS the way in — no trip back to the list.
  await page
    .getByRole("button", { name: /Edit Anthropic — EU gateway/ })
    .first()
    .click();

  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText(/rotate its key/)).toBeVisible();
  await expect(page.getByLabel("Display name")).toHaveValue("Anthropic — EU gateway");
});
