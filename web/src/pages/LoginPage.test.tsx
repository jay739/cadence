import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";
import { api } from "../api/client";
import { useAuth } from "../context/useAuth";
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

vi.mock("../context/useAuth");

const mockedApi = api as unknown as { health: { get: ReturnType<typeof vi.fn> } };
const mockedUseAuth = vi.mocked(useAuth);

function health(navidrome: { configured: boolean } | undefined): HealthResponse {
  return {
    version: "0.1.0",
    mode: "live",
    database_ok: true,
    integrations: {
      prowlarr: { configured: false, reachable: null },
      qbittorrent: { configured: false, reachable: null },
      slskd: { configured: false, reachable: null },
      spotify: { configured: false, reachable: null },
      ...(navidrome ? { navidrome: { configured: navidrome.configured, reachable: null } } : {}),
    },
  };
}

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  const login = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
    login.mockClear().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue({
      user: null,
      loading: false,
      login,
      register: vi.fn(),
      logout: vi.fn(),
    });
  });

  it("does not show a Navidrome tab when it is not configured", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: false }));
    renderLoginPage();
    await screen.findByLabelText("Email");
    expect(screen.queryByRole("tab", { name: /sign in with navidrome/i })).not.toBeInTheDocument();
  });

  it("does not show a Navidrome tab before health has answered", () => {
    // Never resolves within this test; the tab must not flash in optimistically.
    mockedApi.health.get.mockReturnValue(new Promise(() => {}));
    renderLoginPage();
    expect(screen.queryByRole("tab", { name: /sign in with navidrome/i })).not.toBeInTheDocument();
  });

  it("hides the tab if the health check itself fails", async () => {
    mockedApi.health.get.mockRejectedValue(new Error("network error"));
    renderLoginPage();
    await screen.findByLabelText("Email");
    expect(screen.queryByRole("tab", { name: /sign in with navidrome/i })).not.toBeInTheDocument();
  });

  it("shows the tab when Navidrome is configured, defaulting to email mode", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: true }));
    renderLoginPage();
    expect(await screen.findByRole("tab", { name: /sign in with navidrome/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
  });

  it("switching to Navidrome mode relabels the field and drops email validation", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: true }));
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(await screen.findByRole("tab", { name: /sign in with navidrome/i }));

    const field = screen.getByLabelText("Navidrome username");
    expect(field).toHaveAttribute("type", "text");
    expect(field).toHaveAttribute("autoComplete", "username");
  });

  it("switching modes clears whatever was typed, so a stale value is never submitted", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: true }));
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText("Email"), "someone@example.com");
    await user.click(await screen.findByRole("tab", { name: /sign in with navidrome/i }));

    expect(screen.getByLabelText("Navidrome username")).toHaveValue("");
  });

  it("submits whatever identifier was typed, in either mode", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: true }));
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(await screen.findByRole("tab", { name: /sign in with navidrome/i }));
    await user.type(screen.getByLabelText("Navidrome username"), "jay");
    await user.type(screen.getByLabelText("Password"), "hunter2");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(login).toHaveBeenCalledWith("jay", "hunter2");
  });

  it("shows the create-account link only in email mode", async () => {
    mockedApi.health.get.mockResolvedValue(health({ configured: true }));
    const user = userEvent.setup();
    renderLoginPage();

    // The create-account link renders on the very first paint (local mode is the default,
    // unconditional on the health check), so it resolves as soon as this awaits at all and
    // proves nothing about whether the health check has settled yet. Wait for the tab itself,
    // which only appears once navidromeAvailable flips true, before clicking it, or this races
    // against LoginPage's own effect and fails intermittently depending on microtask timing.
    expect(await screen.findByRole("link", { name: /create an account/i })).toBeInTheDocument();

    await user.click(await screen.findByRole("tab", { name: /sign in with navidrome/i }));
    expect(screen.queryByRole("link", { name: /create an account/i })).not.toBeInTheDocument();
  });
});
