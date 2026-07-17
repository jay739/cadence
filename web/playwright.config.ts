import { defineConfig, devices } from "@playwright/test";

/**
 * E2E config: runs against demo mode so the whole flow (search across tabs, request, admin
 * approval, watching a demo fulfillment complete, request history) is exercisable with zero
 * real API keys or live Prowlarr/slskd/qBittorrent instances, per the mission's requirement.
 *
 * `webServer` boots the actual FastAPI backend (which also serves the built web client from
 * backend/static, matching production wiring) in DEMO_MODE against a throwaway SQLite DB.
 */
const externalBaseUrl = process.env.E2E_BASE_URL;

const webServerConfig = externalBaseUrl
  ? undefined
  : {
      command: "npm run e2e:server",
      url: "http://127.0.0.1:8199/api/health",
      reuseExistingServer: false,
      timeout: 30000,
    };

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "html" : "list",
  use: {
    baseURL: externalBaseUrl ?? "http://127.0.0.1:8199",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: webServerConfig,
});
