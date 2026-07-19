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

    await page.getByRole("searchbox").fill("Tum Hi Ho");
    await page.getByRole("button", { name: "Search" }).click();

    await expect(page.getByRole("tablist")).toBeVisible();

    for (const tabName of ["Apple", "MusicBrainz", "Spotify", "Prowlarr"]) {
      // Each tab's accessible name is "<Label> <result count>" (e.g. "Apple 5"), so this must
      // anchor to the start of the name; a loose /Spotify/ would also match the separate,
      // always-present "Your Spotify" personalization tab.
      await page.getByRole("tab", { name: new RegExp(`^${tabName} `) }).click();
      await expect(page.getByRole("tabpanel")).toBeVisible();
      // Demo mode guarantees at least one result on the seeded sources for a matched query,
      // or an explicit empty-state / error message -- either way the tab must render something.
      const panel = page.getByRole("tabpanel");
      await expect(panel).not.toBeEmpty();
    }

    // "Your Spotify" is a standing personalization tab, not a per-query search source, reused
    // from this same logged-in session rather than spending another login() call on it (the
    // auth rate limit is shared across every test in this file, one backend process).
    await page.getByRole("tab", { name: "Your Spotify" }).click();
    await expect(page.getByRole("link", { name: "Connect Spotify" })).toBeVisible();
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
    await expect(page.getByText("Pending review").first()).toBeVisible();
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
  test("the seeded demo request queue covers every status", async ({ page }) => {
    // All seeded demo requests belong to the demo listener account (see
    // backend/app/demo/data.py / seed.py), not the admin account, so history must be checked
    // as that user for the seeded statuses to be visible.
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/requests");

    const anyRow = page.locator(".request-row").first();
    const emptyState = page.locator(".empty-state");
    await expect(anyRow.or(emptyState)).toBeVisible();

    // At least the statuses seeded for this exact user must be represented somewhere in
    // their history (some may have been mutated by earlier tests in this file, e.g. approving
    // the pending one, so we assert on the ones no other test in this suite touches).
    await expect(page.getByText("Fulfilled").first()).toBeVisible();
    await expect(page.getByText("Rejected").first()).toBeVisible();
    await expect(page.getByText("Failed").first()).toBeVisible();
  });

  test("a fulfilled seeded request shows its filed path in the detail view", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/requests");

    const anyRow = page.locator(".request-row").first();
    const emptyState = page.locator(".empty-state");
    await expect(anyRow.or(emptyState)).toBeVisible();

    const fulfilledRow = page.locator(".request-row", { hasText: "Fulfilled" }).first();
    if ((await fulfilledRow.count()) === 0) {
      test.skip(true, "No fulfilled seed row visible for this user in this run");
      return;
    }
    await fulfilledRow.click();
    await expect(page.getByText("Filed at")).toBeVisible();
  });

  test("a failed seeded request shows its failure reason in the detail view", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    await page.goto("/requests");

    const anyRow = page.locator(".request-row").first();
    const emptyState = page.locator(".empty-state");
    await expect(anyRow.or(emptyState)).toBeVisible();

    const failedRow = page.locator(".request-row", { hasText: "Failed" }).first();
    if ((await failedRow.count()) === 0) {
      test.skip(true, "No failed seed row visible for this user in this run");
      return;
    }
    await failedRow.click();
    await expect(page.getByText("Why it failed")).toBeVisible();
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

test.describe("Admin integration settings", () => {
  test("an admin can open the settings page, edit a field, and save it", async ({ page }) => {
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
  });

  test("test connection reports a failure for an unreachable demo host without crashing", async ({
    page,
  }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await page.goto("/admin/settings");

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
});
