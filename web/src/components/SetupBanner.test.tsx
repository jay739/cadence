import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetupBanner } from "./SetupBanner";
import { api } from "../api/client";
import type { HealthResponse } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      health: { get: vi.fn() },
    },
  };
});

const mockedApi = api as unknown as { health: { get: ReturnType<typeof vi.fn> } };

function unconfiguredHealth(): HealthResponse {
  return {
    version: "0.1.0",
    mode: "live",
    database_ok: true,
    integrations: {
      prowlarr: { configured: false, reachable: null },
      qbittorrent: { configured: false, reachable: null },
      slskd: { configured: false, reachable: null },
      spotify: { configured: false, reachable: null },
    },
  };
}

function renderBanner() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SetupBanner />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SetupBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("shows when nothing is configured", async () => {
    mockedApi.health.get.mockResolvedValue(unconfiguredHealth());
    renderBanner();
    expect(await screen.findByRole("status")).toHaveTextContent(/no download sources/i);
  });

  it("stays hidden once at least one integration is configured", async () => {
    const health = unconfiguredHealth();
    health.integrations.prowlarr = { configured: true, reachable: true };
    mockedApi.health.get.mockResolvedValue(health);
    renderBanner();
    // Wait for the query to resolve, then confirm it never rendered.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("stays hidden in demo mode", async () => {
    const health = unconfiguredHealth();
    health.mode = "demo";
    mockedApi.health.get.mockResolvedValue(health);
    renderBanner();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("dismisses for the rest of the session on click", async () => {
    mockedApi.health.get.mockResolvedValue(unconfiguredHealth());
    const user = userEvent.setup();
    renderBanner();
    await screen.findByRole("status");
    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("cadence_setup_banner_dismissed")).toBe("true");
  });

  it("links to the admin settings page", async () => {
    mockedApi.health.get.mockResolvedValue(unconfiguredHealth());
    renderBanner();
    await screen.findByRole("status");
    expect(screen.getByRole("link", { name: /go to settings/i })).toHaveAttribute(
      "href",
      "/admin/settings",
    );
  });
});
