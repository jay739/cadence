import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AdminSettingsPage } from "./AdminSettingsPage";
import { api } from "../api/client";
import type { IntegrationSettings } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      adminSettings: {
        get: vi.fn(),
        update: vi.fn(),
        testConnection: vi.fn(),
        updateSourcePreference: vi.fn(),
      },
    },
  };
});

function baseSettings(): IntegrationSettings {
  return {
    prowlarr: {
      prowlarr_url: { value: null, source: "unset", is_secret: false },
      prowlarr_api_key: { value: null, source: "unset", is_secret: true },
    },
    qbittorrent: {
      qbittorrent_url: { value: null, source: "unset", is_secret: false },
      qbittorrent_username: { value: null, source: "unset", is_secret: false },
      qbittorrent_password: { value: null, source: "unset", is_secret: true },
    },
    slskd: {
      slskd_url: { value: null, source: "unset", is_secret: false },
      slskd_api_key: { value: null, source: "unset", is_secret: true },
    },
    spotify: {
      spotify_client_id: { value: null, source: "unset", is_secret: false },
      spotify_client_secret: { value: null, source: "unset", is_secret: true },
    },
    library: {
      music_library_path: { value: "./data/library", source: "db", is_secret: false },
      library_path_pattern: {
        value: "{artist}/{title}.{ext}",
        source: "db",
        is_secret: false,
      },
    },
    source_preferences: {
      prowlarr: { preference: "env", forced_env: false },
      qbittorrent: { preference: "env", forced_env: false },
      slskd: { preference: "env", forced_env: false },
      spotify: { preference: "env", forced_env: false },
      library: { preference: "env", forced_env: false },
    },
  };
}

function renderWithQueryClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AdminSettingsPage />
    </QueryClientProvider>,
  );
}

describe("AdminSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders every integration section grouped, with masked secrets never shown", async () => {
    const settings = baseSettings();
    settings.prowlarr.prowlarr_api_key = {
      value: "••••••••ab12",
      source: "db",
      is_secret: true,
    };
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();

    expect(await screen.findByText("Prowlarr")).toBeInTheDocument();
    expect(screen.getByText("qBittorrent")).toBeInTheDocument();
    expect(screen.getByText("slskd (Soulseek)")).toBeInTheDocument();
    expect(screen.getByText("Spotify")).toBeInTheDocument();
    expect(screen.getByText("Library")).toBeInTheDocument();

    // The masked value must never appear as an input's actual value (only as a placeholder,
    // which is not what a user types over without noticing).
    const apiKeyInput = document.getElementById("prowlarr-prowlarr_api_key") as HTMLInputElement;
    expect(apiKeyInput.value).toBe("");
    expect(apiKeyInput.placeholder).toBe("••••••••ab12");
  });

  it("shows an env-sourced field as disabled with an explanatory note", async () => {
    const settings = baseSettings();
    settings.prowlarr.prowlarr_url = {
      value: "http://prowlarr:9696",
      source: "env",
      is_secret: false,
    };
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    const urlInput = document.getElementById("prowlarr-prowlarr_url") as HTMLInputElement;
    expect(urlInput).toBeDisabled();
    expect(screen.getByText(/Set via environment variable/)).toBeInTheDocument();
  });

  it("save button stays disabled until a field is edited", async () => {
    vi.mocked(api.adminSettings.get).mockResolvedValue(baseSettings());
    renderWithQueryClient();

    await screen.findByText("Prowlarr");
    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    expect(saveButtons[0]).toBeDisabled();
  });

  it("editing a field enables Save and submits only the changed fields", async () => {
    vi.mocked(api.adminSettings.get).mockResolvedValue(baseSettings());
    vi.mocked(api.adminSettings.update).mockResolvedValue(baseSettings());
    const user = userEvent.setup();

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    const urlInput = document.getElementById("prowlarr-prowlarr_url") as HTMLInputElement;
    await user.type(urlInput, "http://prowlarr:9696");

    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    expect(saveButtons[0]).not.toBeDisabled();
    await user.click(saveButtons[0]);

    await waitFor(() => {
      expect(api.adminSettings.update).toHaveBeenCalledWith("prowlarr", {
        prowlarr_url: "http://prowlarr:9696",
      });
    });
  });

  it("clicking Test connection shows the result message", async () => {
    vi.mocked(api.adminSettings.get).mockResolvedValue(baseSettings());
    vi.mocked(api.adminSettings.testConnection).mockResolvedValue({
      ok: true,
      detail: "Connected to Prowlarr successfully",
    });
    const user = userEvent.setup();

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    const testButtons = screen.getAllByRole("button", { name: "Test connection" });
    await user.click(testButtons[0]);

    expect(await screen.findByText("Connected to Prowlarr successfully")).toBeInTheDocument();
  });

  it("does not offer a Test connection button for the Library section", async () => {
    vi.mocked(api.adminSettings.get).mockResolvedValue(baseSettings());
    renderWithQueryClient();
    await screen.findByText("Library");

    // Only 4 testable integrations (Prowlarr, qBittorrent, slskd, Spotify); Library has none.
    const testButtons = screen.getAllByRole("button", { name: "Test connection" });
    expect(testButtons).toHaveLength(4);
  });

  it("choosing 'Use these settings' saves the source preference for that integration", async () => {
    const settings = baseSettings();
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);
    vi.mocked(api.adminSettings.updateSourcePreference).mockResolvedValue(settings);
    const user = userEvent.setup();

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    const databaseRadios = screen.getAllByRole("radio", { name: "Use these settings" });
    await user.click(databaseRadios[0]);

    await waitFor(() => {
      expect(api.adminSettings.updateSourcePreference).toHaveBeenCalledWith("prowlarr", "database");
    });
  });

  it("shows the break-glass override note and reflects the stored preference despite it", async () => {
    const settings = baseSettings();
    settings.source_preferences.prowlarr = { preference: "database", forced_env: true };
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    expect(screen.getByText(/SETTINGS_FORCE_ENV_PRECEDENCE is set/)).toBeInTheDocument();
    const databaseRadios = screen.getAllByRole("radio", { name: "Use these settings" });
    expect(databaseRadios[0]).toBeChecked();
  });
});
