import { test, expect, type Page } from "@playwright/test";

// THROWAWAY visual-verification script. Not part of the suite; deleted before commit.

const SHOTS = "/tmp/claude-1000/-home-jay739/e98ba5e6-8d13-48c3-83db-7585879b5a74/scratchpad/shots";

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

function suite(theme: "dark" | "light") {
  test(`${theme}: surfaces`, async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);

    // --- theme toggle: rest, then hover ---
    const toggle = page.locator(".theme-toggle");
    await page.screenshot({
      path: `${SHOTS}/${theme}-01-header.png`,
      clip: await clipOf(page, "header, .app-shell__header"),
    });
    await toggle.hover();
    await page.waitForTimeout(400);
    await toggle.screenshot({ path: `${SHOTS}/${theme}-02-toggle-hover.png` });

    const hoveredTransform = await toggle.evaluate((el) => getComputedStyle(el).transform);
    console.log(`[${theme}] toggle transform while hovered: ${hoveredTransform}`);

    // Move the pointer away so the rest state is clean for later shots.
    await page.mouse.move(0, 0);
    await page.waitForTimeout(400);

    // --- track cards ---
    await page.getByRole("searchbox").fill("Tum Hi Ho");
    await expect(page.getByRole("tab", { name: /^Apple \d/ })).toBeVisible();
    await page.waitForTimeout(300);
    const firstTrack = page.locator(".track-card").first();
    await expect(firstTrack).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/${theme}-03-tracks-rest.png` });
    await firstTrack.hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-04-tracks-hover.png` });

    // hover the primary button inside that row
    await firstTrack
      .getByRole("button", { name: /request/i })
      .first()
      .hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-05-button-hover.png` });

    // --- request rows ---
    await page.getByRole("link", { name: "Your requests" }).click();
    await expect(page).toHaveURL(/\/requests/);
    const firstRow = page.locator(".request-row").first();
    await expect(firstRow).toBeVisible();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/${theme}-06-requests-rest.png` });
    await firstRow.hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-07-requests-hover.png` });

    // --- profile / stat tiles ---
    await page.getByRole("link", { name: "Profile" }).click();
    await expect(page).toHaveURL(/\/profile/);
    const tile = page.locator(".stat-tile").nth(1);
    await expect(tile).toBeVisible();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/${theme}-08-profile-rest.png`, fullPage: true });
    await tile.hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-09-profile-hover.png` });
  });

  test(`${theme}: admin surfaces`, async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

    await page.getByRole("link", { name: "Approval queue" }).click();
    const qrow = page.locator(".queue-row").first();
    await expect(qrow).toBeVisible();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/${theme}-10-queue-rest.png` });
    await qrow.hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-11-queue-hover.png` });

    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/admin\/settings$/);
    const section = page.locator(".settings-section").first();
    await expect(section).toBeVisible();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/${theme}-12-settings-rest.png` });
    await section.hover();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/${theme}-13-settings-hover.png` });
  });
}

async function clipOf(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  return box ?? { x: 0, y: 0, width: 800, height: 120 };
}

test.describe("visual dark", () => {
  test.use({ colorScheme: "dark" });
  suite("dark");

  // Proof that the crossfade rotation is actually legible: sample the moon icon's transform
  // partway through the switch and confirm it is mid-arc while already fully opaque.
  test("dark: toggle crossfade is mid-rotation while visible", async ({ page }) => {
    await login(page, USER_EMAIL, USER_PASSWORD);
    const toggle = page.locator(".theme-toggle");

    console.log(
      "[media] prefers-reduced-motion: reduce =",
      await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
    );

    console.log(
      "[diag] " +
        JSON.stringify(
          await page.evaluate(() => {
            const root = getComputedStyle(document.documentElement);
            const sun = document.querySelector(".theme-toggle__sun")!;
            const btn = document.querySelector(".theme-toggle")!;
            return {
              easeToken: root.getPropertyValue("--ease-out-soft"),
              motionToken: root.getPropertyValue("--motion-hover"),
              sunTransition: getComputedStyle(sun).transition,
              sunTransitionDuration: getComputedStyle(sun).transitionDuration,
              btnTransition: getComputedStyle(btn).transition,
            };
          }),
          null,
          1,
        ),
    );

    // dark -> light: the sun is the incoming icon. Sampled per animation frame from inside the
    // page, and the click is dispatched in the same task the sampler starts, so no CDP
    // round-trip sits between the theme flipping and the first reading.
    const frames = await page.evaluate(async () => {
      const sun = document.querySelector(".theme-toggle__sun") as SVGElement;
      const btn = document.querySelector(".theme-toggle") as HTMLElement;
      const out: string[] = [];
      const t0 = performance.now();
      btn.click();
      await new Promise<void>((resolve) => {
        function frame() {
          const cs = getComputedStyle(sun);
          const m = new DOMMatrixReadOnly(cs.transform);
          const deg = (Math.atan2(m.b, m.a) * 180) / Math.PI;
          const scale = Math.hypot(m.a, m.b);
          out.push(
            `  t=${Math.round(performance.now() - t0)}ms rot=${deg.toFixed(1)}deg ` +
              `scale=${scale.toFixed(2)} opacity=${cs.opacity} anims=${document.getAnimations().length}`,
          );
          if (performance.now() - t0 < 520) requestAnimationFrame(frame);
          else resolve();
        }
        requestAnimationFrame(frame);
      });
      return out;
    });
    // Thinned to every third frame so the log stays readable.
    console.log(
      "[crossfade] incoming sun, per frame:\n" + frames.filter((_, i) => i % 3 === 0).join("\n"),
    );

    await page.screenshot({ path: `${SHOTS}/dark-14-after-toggle.png` });
  });
});

test.describe("visual light", () => {
  test.use({ colorScheme: "light" });
  suite("light");
});
