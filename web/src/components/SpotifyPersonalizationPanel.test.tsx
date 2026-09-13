import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SpotifyPersonalizationPanel } from "./SpotifyPersonalizationPanel";
import { api } from "../api/client";
import type { SpotifyConnectionStatus, SpotifyPlaylistsResponse, TrackResult } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      health: { get: vi.fn() },
      spotifyPersonalization: {
        status: vi.fn(),
        connectUrl: vi.fn(() => "/api/spotify/connect"),
        disconnect: vi.fn(),
        savedTracks: vi.fn(),
        playlists: vi.fn(),
        playlistTracks: vi.fn(),
      },
    },
  };
});

const mockedApi = api as unknown as {
  health: { get: ReturnType<typeof vi.fn> };
  spotifyPersonalization: {
    status: ReturnType<typeof vi.fn>;
    connectUrl: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    savedTracks: ReturnType<typeof vi.fn>;
    playlists: ReturnType<typeof vi.fn>;
    playlistTracks: ReturnType<typeof vi.fn>;
  };
};

function healthWithSpotify(configured: boolean) {
  return {
    version: "0.1.0",
    mode: "live" as const,
    database_ok: true,
    navidrome_login_available: false,
    spotify_search_available: configured,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SpotifyPersonalizationPanel onRequest={vi.fn()} requestStates={{}} />
    </QueryClientProvider>,
  );
}

const track: TrackResult = {
  source: "spotify",
  source_id: "trk1",
  title: "A Great Song",
  artist: "Some Artist",
  album: "Some Album",
  duration_seconds: 210,
  artwork_url: null,
  year: 2021,
  musicbrainz_recording_id: null,
  indexer: null,
  size_bytes: null,
  seeders: null,
  download_url: null,
  guid: null,
};

describe("SpotifyPersonalizationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.health.get.mockResolvedValue(healthWithSpotify(true));
    mockedApi.spotifyPersonalization.playlists.mockResolvedValue({
      playlists: [],
    } satisfies SpotifyPlaylistsResponse);
  });

  it("shows a Connect Spotify prompt when not connected", async () => {
    mockedApi.spotifyPersonalization.status.mockResolvedValue({
      connected: false,
      display_name: null,
    } satisfies SpotifyConnectionStatus);

    renderPanel();

    expect(await screen.findByText(/connect your spotify account/i)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /connect spotify/i });
    expect(link).toHaveAttribute("href", "/api/spotify/connect");
  });

  it("explains instead of offering a dead-end Connect button when the server has no Spotify app", async () => {
    mockedApi.health.get.mockResolvedValue(healthWithSpotify(false));
    mockedApi.spotifyPersonalization.status.mockResolvedValue({
      connected: false,
      display_name: null,
    } satisfies SpotifyConnectionStatus);

    renderPanel();

    expect(await screen.findByText(/isn't set up on this server yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /connect spotify/i })).not.toBeInTheDocument();
  });

  it("shows saved tracks and the display name once connected", async () => {
    mockedApi.spotifyPersonalization.status.mockResolvedValue({
      connected: true,
      display_name: "Test Listener",
    } satisfies SpotifyConnectionStatus);
    mockedApi.spotifyPersonalization.savedTracks.mockResolvedValue([track]);

    renderPanel();

    expect(await screen.findByText(/connected as test listener/i)).toBeInTheDocument();
    expect(await screen.findByText("A Great Song")).toBeInTheDocument();
  });

  it("shows an empty state when connected but nothing is saved", async () => {
    mockedApi.spotifyPersonalization.status.mockResolvedValue({
      connected: true,
      display_name: null,
    } satisfies SpotifyConnectionStatus);
    mockedApi.spotifyPersonalization.savedTracks.mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByText(/nothing here yet/i)).toBeInTheDocument();
  });

  it("lists playlists as sub-tabs and loads a playlist's tracks on click", async () => {
    mockedApi.spotifyPersonalization.status.mockResolvedValue({
      connected: true,
      display_name: null,
    } satisfies SpotifyConnectionStatus);
    mockedApi.spotifyPersonalization.savedTracks.mockResolvedValue([]);
    mockedApi.spotifyPersonalization.playlists.mockResolvedValue({
      playlists: [{ id: "pl1", name: "Roadtrip", track_count: 3 }],
    } satisfies SpotifyPlaylistsResponse);
    mockedApi.spotifyPersonalization.playlistTracks.mockResolvedValue([
      { ...track, source_id: "trk-playlist" },
    ]);

    const user = userEvent.setup();
    renderPanel();

    const playlistTab = await screen.findByRole("tab", { name: /roadtrip/i });
    await user.click(playlistTab);

    await waitFor(() => {
      expect(mockedApi.spotifyPersonalization.playlistTracks).toHaveBeenCalledWith("pl1");
    });
    expect(await screen.findByText("A Great Song")).toBeInTheDocument();
  });

  it("disconnects and returns to the connect prompt", async () => {
    mockedApi.spotifyPersonalization.status
      .mockResolvedValueOnce({
        connected: true,
        display_name: "Test Listener",
      } satisfies SpotifyConnectionStatus)
      .mockResolvedValueOnce({
        connected: false,
        display_name: null,
      } satisfies SpotifyConnectionStatus);
    mockedApi.spotifyPersonalization.savedTracks.mockResolvedValue([]);
    mockedApi.spotifyPersonalization.disconnect.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPanel();

    const disconnectButton = await screen.findByRole("button", { name: /disconnect/i });
    await user.click(disconnectButton);

    expect(mockedApi.spotifyPersonalization.disconnect).toHaveBeenCalled();
    expect(await screen.findByText(/connect your spotify account/i)).toBeInTheDocument();
  });
});
