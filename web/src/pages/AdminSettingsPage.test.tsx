import { render, screen, waitFor, within } from "@testing-library/react";
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

/**
 * An install that is past first run: one integration actually configured, which is what makes
 * the setup-time precedence card stand down and leaves only the per-section disclosures.
 */
function configuredSettings(): IntegrationSettings {
  const settings = baseSettings();
  settings.prowlarr.prowlarr_url = {
    value: "http://prowlarr:9696",
    source: "db",
    is_secret: false,
  };
  return settings;
}

/** The one integration's card, so a query can't wander into another section's identical control. */
function sectionFor(title: string): HTMLElement {
  // A string `name` is matched against the whole accessible name, so this can't stray into a
  // section that merely mentions another integration in its description.
  const heading = screen.getByRole("heading", { name: title });
  const section = heading.closest("section");
  if (!section) throw new Error(`No settings section found for ${title}`);
  return section as HTMLElement;
}

/** That section's collapsed "Advanced" disclosure, where the source choice now lives. */
function advancedFor(title: string): HTMLElement {
  const details = sectionFor(title).querySelector("details.settings-advanced");
  if (!details) throw new Error(`No Advanced disclosure found for ${title}`);
  return details as HTMLElement;
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

  it("keeps the source choice out of the always-visible layout, behind each section's Advanced", async () => {
    const settings = configuredSettings();
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    // Five sections no longer spend five radio pairs' worth of vertical space on a question an
    // operator answers about once.
    for (const title of ["Prowlarr", "qBittorrent", "slskd (Soulseek)", "Spotify", "Library"]) {
      expect(advancedFor(title)).not.toHaveAttribute("open");
    }
    // Collapsed, but not opaque: what each install is actually obeying reads off the summary.
    expect(within(advancedFor("Prowlarr")).getByText("environment wins")).toBeInTheDocument();
  });

  it("choosing 'Use these settings' under Advanced saves that one integration's preference", async () => {
    const settings = configuredSettings();
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);
    vi.mocked(api.adminSettings.updateSourcePreference).mockResolvedValue(settings);
    const user = userEvent.setup();

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    const advanced = advancedFor("Prowlarr");
    await user.click(within(advanced).getByText("Advanced"));
    await user.click(within(advanced).getByRole("radio", { name: "Use these settings" }));

    await waitFor(() => {
      expect(api.adminSettings.updateSourcePreference).toHaveBeenCalledWith("prowlarr", "database");
    });
    // Strictly that one integration: the per-section control is still per-section.
    expect(api.adminSettings.updateSourcePreference).toHaveBeenCalledTimes(1);
  });

  it("never collapses the break-glass override out of sight", async () => {
    const settings = configuredSettings();
    settings.source_preferences.prowlarr = { preference: "database", forced_env: true };
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    // Showing a selected radio the app isn't obeying is worse than the clutter this disclosure
    // removes, so a forced install opens the panel instead of hiding the reason why.
    const advanced = advancedFor("Prowlarr");
    expect(advanced).toHaveAttribute("open");
    expect(within(advanced).getByText(/SETTINGS_FORCE_ENV_PRECEDENCE is set/)).toBeInTheDocument();
    expect(within(advanced).getByText("environment (forced)")).toBeInTheDocument();
    // …and the stored choice is still reported honestly, not rewritten to match what's forced.
    expect(within(advanced).getByRole("radio", { name: "Use these settings" })).toBeChecked();
  });
});

describe("AdminSettingsPage first-run source precedence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asks the question once during setup, and answers it for every integration", async () => {
    const settings = baseSettings();
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);
    vi.mocked(api.adminSettings.updateSourcePreference).mockResolvedValue(settings);
    const user = userEvent.setup();

    renderWithQueryClient();
    const card = (await screen.findByText(/where should these settings come from/i)).closest(
      "section",
    ) as HTMLElement;

    await user.click(within(card).getByRole("radio", { name: "Use these settings" }));

    await waitFor(() => {
      expect(api.adminSettings.updateSourcePreference).toHaveBeenCalledTimes(5);
    });
    for (const key of ["prowlarr", "qbittorrent", "slskd", "spotify", "library"]) {
      expect(api.adminSettings.updateSourcePreference).toHaveBeenCalledWith(key, "database");
    }
  });

  it("says so when integrations disagree rather than showing one of them as selected", async () => {
    const settings = baseSettings();
    settings.source_preferences.spotify = { preference: "database", forced_env: false };
    vi.mocked(api.adminSettings.get).mockResolvedValue(settings);

    renderWithQueryClient();
    const card = (await screen.findByText(/where should these settings come from/i)).closest(
      "section",
    ) as HTMLElement;

    expect(within(card).getByText(/currently disagree/i)).toBeInTheDocument();
    expect(within(card).getByRole("radio", { name: "Use environment" })).not.toBeChecked();
    expect(within(card).getByRole("radio", { name: "Use these settings" })).not.toBeChecked();
  });

  it("disappears once anything is configured, leaving only the per-section Advanced", async () => {
    vi.mocked(api.adminSettings.get).mockResolvedValue(configuredSettings());

    renderWithQueryClient();
    await screen.findByText("Prowlarr");

    expect(screen.queryByText(/where should these settings come from/i)).not.toBeInTheDocument();
    expect(advancedFor("Prowlarr")).toBeInTheDocument();
  });
});
