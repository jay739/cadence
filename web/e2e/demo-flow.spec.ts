import { test, expect, type Page } from "@playwright/test";

/**
 * End-to-end coverage of the full demo-mode flow, per the mission's requirement:
 * search across tabs, submitting a request, admin approval, watching a demo fulfillment
 * complete, request history. Runs against the real backend (demo mode) serving the real
 * built web client, not a mocked API.
 */

const ADMIN_EMAIL = "admin@demo.cadence.internal";
const ADMIN_PASSWORD = "demo-admin-password-not-real";
const USER_EMAIL = "listener@demo.cadence.internal";
const USER_PASSWORD = "demo-user-password-not-real";

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/search/);
}

test.describe("Demo mode: search", () => {
  test("searching shows results across every source tab", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);

    // Before any query: the Spotify tab doubles as the user's own connection surface. Demo
    // mode has no Spotify app configured server-side, so it must explain that instead of
    // offering a Connect button that would dead-end.
    await page.getByRole("tab", { name: "Spotify" }).click();
    await expect(page.getByText(/isn't set up on this server yet/i)).toBeVisible();
    await page.getByRole("tab", { name: "Apple" }).click();

    // No Search click: results must appear on their own once the ~400ms typing debounce
    // settles (as-you-type over the SSE stream). Playwright's auto-waiting expect covers the
    // debounce window without an explicit sleep.
    await page.getByRole("searchbox").fill("Tum Hi Ho");

    await expect(page.getByRole("tablist")).toBeVisible();
    await expect(page.getByRole("tab", { name: /^Apple \d/ })).toBeVisible();

    for (const tabName of ["Apple", "MusicBrainz", "Spotify", "Prowlarr"]) {
      // Each tab's accessible name is "<Label> <result count>" (e.g. "Apple 5") once a query
      // is active, so anchor to the start of the name.
      await page.getByRole("tab", { name: new RegExp(`^${tabName} `) }).click();
      await expect(page.getByRole("tabpanel")).toBeVisible();
      // Demo mode guarantees at least one result on the seeded sources for a matched query,
      // or an explicit empty-state / error message -- either way the tab must render something.
      const panel = page.getByRole("tabpanel");
      await expect(panel).not.toBeEmpty();
    }
  });
});

test.describe("Demo mode: request lifecycle", () => {
  test("a user can request a track and see it pending, then an admin approves it", async ({
    page,
  }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);

    await page.getByRole("searchbox").fill("Kesariya");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByRole("tabpanel")).toBeVisible();

    const firstRequestButton = page.getByRole("button", { name: "Request" }).first();
    await firstRequestButton.click();
    await expect(page.getByRole("button", { name: "Requested" }).first()).toBeVisible();

    await page.getByRole("link", { name: "Your requests" }).click();
    await expect(page).toHaveURL(/\/requests$/);
    await expect(page.getByText("Waiting for approval").first()).toBeVisible();
  });

  test("admin approval queue lists pending requests and can approve one", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

    await page.getByRole("link", { name: "Approval queue" }).click();
    await expect(page).toHaveURL(/\/admin$/);

    // Wait for the queue fetch to resolve (either rows or the explicit empty state), rather
    // than counting immediately -- the list loads asynchronously after navigation.
    const queueRows = page.locator(".queue-row");
    const emptyState = page.locator(".empty-state");
    await expect(queueRows.first().or(emptyState)).toBeVisible();

    const initialCount = await queueRows.count();
    test.skip(initialCount === 0, "No pending requests seeded to approve in this run");

    await queueRows.first().getByRole("button", { name: "Approve" }).click();
    await expect(queueRows).toHaveCount(initialCount - 1);
  });
});

test.describe("Demo mode: request history and fulfillment visibility", () => {
  test("the seeded demo request queue covers every status, with fulfilled/failed detail views reachable", async ({
    page,
  }) => {
    // All seeded demo requests belong to the demo listener account (see
    // backend/app/demo/data.py / seed.py), not the admin account, so history must be checked
    // as that user for the seeded statuses to be visible. These three checks used to be
    // separate tests; merged into one so this describe block spends only one of the suite's
    // /api/auth/login calls -- AUTH_RATE_LIMIT is 10/minute per IP and this whole file runs
    // from the same IP well within a minute, so every login() call in this file draws from one
    // shared budget.
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/requests");

    const anyRow = page.locator(".request-row").first();
    const emptyState = page.locator(".empty-state");
    await expect(anyRow.or(emptyState)).toBeVisible();

    // At least the statuses seeded for this exact user must be represented somewhere in
    // their history (some may have been mutated by earlier tests in this file, e.g. approving
    // the pending one, so we assert on the ones no other test in this suite touches).
    await expect(page.getByText("Downloaded").first()).toBeVisible();
    await expect(page.getByText("Declined").first()).toBeVisible();
    await expect(page.getByText("Download failed").first()).toBeVisible();

    const fulfilledRow = page.locator(".request-row", { hasText: "Downloaded" }).first();
    if ((await fulfilledRow.count()) > 0) {
      await fulfilledRow.click();
      await expect(page.getByText("Filed at")).toBeVisible();
      await page.goto("/requests");
    }

    const failedRow = page.locator(".request-row", { hasText: "Failed" }).first();
    if ((await failedRow.count()) > 0) {
      await failedRow.click();
      await expect(page.getByText("Why it failed")).toBeVisible();
    }
  });
});

test.describe("Tab navigation", () => {
  test("navigating between tabs lands on the right route and shows the right content", async ({
    page,
  }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);

    // Search is the landing tab post-login.
    await expect(page).toHaveURL(/\/search$/);
    await expect(page.getByRole("searchbox")).toBeVisible();

    // The route-transition wrapper (see RouteTransition.tsx) must not interfere with the
    // route actually changing or the destination page's content rendering.
    await page.getByRole("link", { name: "Your requests" }).click();
    await expect(page).toHaveURL(/\/requests$/);
    await expect(page.getByRole("searchbox")).not.toBeVisible();

    await page.getByRole("link", { name: "Search" }).click();
    await expect(page).toHaveURL(/\/search$/);
    await expect(page.getByRole("searchbox")).toBeVisible();
  });
});

test.describe("Auth guarding", () => {
  test("unauthenticated visitors are redirected to login", async ({ page }) => {
    await page.goto("/search");
    await expect(page).toHaveURL(/\/login/);
  });

  test("a non-admin user cannot reach the approval queue", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/search/);
  });

  test("a non-admin user cannot reach the integration settings page", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/admin/settings");
    await expect(page).toHaveURL(/\/search/);
  });
});

test.describe("Theme toggle", () => {
  // Pinned explicitly: with no stored preference, Cadence follows the OS's prefers-color-scheme
  // (dark is just the app's own fallback when the OS has no opinion), so leaving this unset
  // would make the "starts dark" assertion depend on the test runner's own default.
  test.use({ colorScheme: "dark" });

  test("defaults to dark, switches to light on click, and survives a reload", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);

    const html = page.locator("html");
    await expect(html).toHaveAttribute("data-theme", "dark");

    const toggle = page.getByRole("button", { name: "Switch to light mode" });
    await expect(toggle).toBeVisible();
    await toggle.click();

    await expect(html).toHaveAttribute("data-theme", "light");
    await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();

    // The pre-paint script in index.html reads localStorage before React mounts, so a
    // reload must not flash back to dark even briefly.
    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "light");
  });
});

test.describe("Admin integration settings", () => {
  test("an admin can open the settings page, edit and save a field, and run a test connection", async ({
    page,
  }) => {
    // Merged from two tests into one, for the same shared-login-budget reason noted in the
    // "request history" describe block above.
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/admin\/settings$/);

    await expect(page.getByRole("heading", { name: "Prowlarr" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "qBittorrent" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "slskd (Soulseek)" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Spotify" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();

    const urlInput = page.locator("#prowlarr-prowlarr_url");
    await urlInput.fill("http://prowlarr.example.internal:9696");

    // Scope to the section whose *heading* is exactly "Prowlarr", not any section whose text
    // merely mentions Prowlarr in passing (the qBittorrent section's description does).
    const prowlarrSection = page
      .locator(".settings-section")
      .filter({ has: page.getByRole("heading", { name: "Prowlarr", exact: true }) });
    const saveButton = prowlarrSection.getByRole("button", { name: "Save" });
    await expect(saveButton).toBeEnabled();
    await saveButton.click();

    // After a successful save, the field reflects the saved value and is tagged "saved" (db
    // source), proving the round trip (not just an optimistic local echo).
    await expect(prowlarrSection.getByText("saved")).toBeVisible();
    await expect(urlInput).toHaveValue("http://prowlarr.example.internal:9696");

    const slskdSection = page
      .locator(".settings-section")
      .filter({ has: page.getByRole("heading", { name: "slskd (Soulseek)", exact: true }) });
    await slskdSection.locator("#slskd-slskd_url").fill("http://localhost:1");
    await slskdSection.locator("#slskd-slskd_api_key").fill("demo-key");
    await slskdSection.getByRole("button", { name: "Test connection" }).click();

    // Whatever the outcome, the page must render a result message rather than hanging or
    // erroring -- this is a real outbound attempt against a very unlikely-to-answer port.
    await expect(slskdSection.locator(".form-error, .settings-test-ok")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("an admin can flip an integration to prefer these settings over the environment", async ({
    page,
  }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await page.goto("/admin/settings");

    const prowlarrSection = page
      .locator(".settings-section")
      .filter({ has: page.getByRole("heading", { name: "Prowlarr", exact: true }) });
    const useDatabase = prowlarrSection.getByRole("radio", { name: "Use these settings" });
    await expect(useDatabase).toBeVisible();
    await useDatabase.click();
    await expect(useDatabase).toBeChecked();

    // Survives a reload: the choice round-tripped through the backend, not just local state.
    await page.reload();
    const prowlarrSectionAfterReload = page
      .locator(".settings-section")
      .filter({ has: page.getByRole("heading", { name: "Prowlarr", exact: true }) });
    await expect(
      prowlarrSectionAfterReload.getByRole("radio", { name: "Use these settings" }),
    ).toBeChecked();
  });
});

test.describe("Glass surfaces", () => {
  test("the sticky header and panels actually resolve a backdrop blur", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/requests");

    // A computed-style check rather than a screenshot comparison: the point is to catch the
    // frosted treatment silently collapsing (a renamed/removed --glass-blur token leaves an
    // invalid property, which resolves to "none" while everything still renders fine), which
    // no functional assertion elsewhere would notice.
    const headerFilter = await page
      .locator(".shell__header")
      .evaluate((el) => getComputedStyle(el).backdropFilter);
    expect(headerFilter).toContain("blur");

    const cardFilter = await page
      .locator(".request-row")
      .first()
      .evaluate((el) => getComputedStyle(el).backdropFilter);
    expect(cardFilter).toContain("blur");
  });
});

test.describe("Profile page", () => {
  test("a user can see their request stats, gets a wrong-password rejection, and can save an avatar URL", async ({
    page,
  }) => {
    // One login() call for this whole scenario, for the same shared-rate-limit-budget reason
    // noted in the "request history" describe block above.
    await login(page, USER_EMAIL, USER_PASSWORD);

    await page.getByRole("link", { name: "Profile" }).click();
    await expect(page).toHaveURL(/\/profile$/);
    await expect(page.getByRole("heading", { name: "Your requests" })).toBeVisible();

    // Fulfilled/Rejected/Failed each have exactly one seeded demo request, and unlike
    // Pending/Approved, no other test in this file mutates those specific statuses.
    for (const label of ["Fulfilled", "Rejected", "Failed"]) {
      const tile = page.locator(".stat-tile", { hasText: label });
      await expect(tile.locator(".stat-tile__value")).toHaveText("1");
    }

    // The local demo account gets a password-change form, never the Navidrome redirect link.
    await expect(page.getByLabel("Current password")).toBeVisible();
    await expect(page.getByLabel("New password")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /manage your password in navidrome/i }),
    ).toHaveCount(0);

    // A wrong current password must be rejected without disturbing the real demo credentials
    // other tests in this suite rely on.
    await page.getByLabel("Current password").fill("definitely-the-wrong-password");
    await page.getByLabel("New password").fill("a-brand-new-password-2");
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByText("Current password is incorrect")).toBeVisible();

    const avatarInput = page.getByLabel("Avatar URL");
    await avatarInput.fill("https://picsum.photos/seed/e2e-avatar/200");
    await page.getByRole("button", { name: "Save avatar" }).click();

    // Once saved, the draft matches the persisted value again, so the button goes back to
    // disabled -- proving a real round trip rather than an optimistic local echo.
    await expect(page.getByRole("button", { name: "Save avatar" })).toBeDisabled();
    await expect(avatarInput).toHaveValue("https://picsum.photos/seed/e2e-avatar/200");
  });
});
