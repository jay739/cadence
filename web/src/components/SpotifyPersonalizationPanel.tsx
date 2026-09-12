import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { PreferredFormat, TrackResult } from "../types/api";
import { TrackCard } from "./TrackCard";

type RequestState = "idle" | "requesting" | "requested" | "error";
type LibraryView = "saved-tracks" | { playlistId: string; playlistName: string };

function trackKey(track: TrackResult): string {
  return `${track.source}:${track.source_id}`;
}

interface SpotifyPersonalizationPanelProps {
  onRequest: (track: TrackResult, preferredFormat: PreferredFormat) => Promise<void>;
  requestStates: Record<string, RequestState>;
}

export function SpotifyPersonalizationPanel({
  onRequest,
  requestStates,
}: SpotifyPersonalizationPanelProps) {
  const [view, setView] = useState<LibraryView>("saved-tracks");
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: ["spotify-personalization-status"],
    queryFn: () => api.spotifyPersonalization.status(),
  });

  // Same cache key the SetupBanner uses, so this costs no extra request in practice. Needed to
  // tell "you haven't connected yet" apart from "this server has no Spotify app configured at
  // all", where showing a Connect button would just dead-end in an error.
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health.get(),
    staleTime: 60_000,
  });

  // Spotify's own callback redirects the whole browser back to "/" with this query param, since
  // the OAuth dance is a top-level navigation, not something this SPA can intercept as a fetch.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("spotify_connect");
    if (result === "success" || result === "error") {
      params.delete("spotify_connect");
      const next = params.toString();
      window.history.replaceState({}, "", next ? `?${next}` : window.location.pathname);
      queryClient.invalidateQueries({ queryKey: ["spotify-personalization-status"] });
    }
  }, [queryClient]);

  const disconnectMutation = useMutation({
    mutationFn: () => api.spotifyPersonalization.disconnect(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["spotify-personalization-status"] });
      setView("saved-tracks");
    },
  });

  const savedTracksQuery = useQuery({
    queryKey: ["spotify-saved-tracks"],
    queryFn: () => api.spotifyPersonalization.savedTracks(),
    enabled: statusQuery.data?.connected === true && view === "saved-tracks",
  });

  const playlistsQuery = useQuery({
    queryKey: ["spotify-playlists"],
    queryFn: () => api.spotifyPersonalization.playlists(),
    enabled: statusQuery.data?.connected === true,
  });

  const playlistTracksQuery = useQuery({
    queryKey: ["spotify-playlist-tracks", typeof view === "object" ? view.playlistId : null],
    queryFn: () =>
      api.spotifyPersonalization.playlistTracks(typeof view === "object" ? view.playlistId : ""),
    enabled: typeof view === "object",
  });

  if (statusQuery.isLoading) {
    return <p>Checking your Spotify connection…</p>;
  }

  if (!statusQuery.data?.connected) {
    const spotifyConfigured = healthQuery.data?.spotify_search_available ?? true;
    if (!spotifyConfigured) {
      return (
        <div className="empty-state spotify-connect-prompt">
          <p>
            Spotify isn't set up on this server yet. An admin needs to add the Spotify app details
            on the Settings page before anyone can connect an account or search Spotify.
          </p>
        </div>
      );
    }
    return (
      <div className="empty-state spotify-connect-prompt">
        <p>
          Connect your Spotify account to search Spotify and quick-pick from your saved tracks and
          playlists.
        </p>
        <a className="button button--primary" href={api.spotifyPersonalization.connectUrl()}>
          Connect Spotify
        </a>
      </div>
    );
  }

  const activeQuery = view === "saved-tracks" ? savedTracksQuery : playlistTracksQuery;
  const tracks: TrackResult[] = activeQuery.data ?? [];

  return (
    <div className="spotify-personalization">
      <div className="spotify-personalization__header">
        <span>
          Connected{statusQuery.data.display_name ? ` as ${statusQuery.data.display_name}` : ""}
        </span>
        <button
          type="button"
          className="button button--ghost"
          onClick={() => disconnectMutation.mutate()}
          disabled={disconnectMutation.isPending}
        >
          {disconnectMutation.isPending ? "Disconnecting…" : "Disconnect"}
        </button>
      </div>

      <div
        className="spotify-personalization__subtabs"
        role="tablist"
        aria-label="Your Spotify library"
      >
        <button
          type="button"
          role="tab"
          aria-selected={view === "saved-tracks"}
          className={`search-tabs__tab ${view === "saved-tracks" ? "search-tabs__tab--active" : ""}`}
          onClick={() => setView("saved-tracks")}
        >
          Saved tracks
        </button>
        {playlistsQuery.data?.playlists.map((playlist) => (
          <button
            key={playlist.id}
            type="button"
            role="tab"
            aria-selected={typeof view === "object" && view.playlistId === playlist.id}
            className={`search-tabs__tab ${
              typeof view === "object" && view.playlistId === playlist.id
                ? "search-tabs__tab--active"
                : ""
            }`}
            onClick={() => setView({ playlistId: playlist.id, playlistName: playlist.name })}
          >
            {playlist.name}
            <span className="search-tabs__count">{playlist.track_count}</span>
          </button>
        ))}
      </div>

      {activeQuery.isLoading && <p>Loading…</p>}
      {activeQuery.isError && (
        <p className="form-error">
          {activeQuery.error instanceof ApiError
            ? activeQuery.error.message
            : "Could not load this from Spotify."}
        </p>
      )}
      {activeQuery.isSuccess && tracks.length === 0 && (
        <div className="empty-state">Nothing here yet.</div>
      )}
      {activeQuery.isSuccess && tracks.length > 0 && (
        <ul className="track-list">
          {tracks.map((track) => (
            <TrackCard
              key={trackKey(track)}
              track={track}
              onRequest={onRequest}
              requestState={requestStates[trackKey(track)] ?? "idle"}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
