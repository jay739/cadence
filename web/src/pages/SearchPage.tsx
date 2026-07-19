import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { SearchSourceName, TrackResult } from "../types/api";
import { TrackCard } from "../components/TrackCard";
import { SpotifyPersonalizationPanel } from "../components/SpotifyPersonalizationPanel";
import "./SearchPage.css";

const SOURCE_TABS: { key: SearchSourceName; label: string }[] = [
  { key: "apple", label: "Apple" },
  { key: "musicbrainz", label: "MusicBrainz" },
  { key: "spotify", label: "Spotify" },
  { key: "prowlarr", label: "Prowlarr" },
];

// Not a search source: an always-available quick-pick browse of the signed-in user's own
// Spotify library (opt-in, separate OAuth connection), additive to the four search tabs above.
const YOUR_SPOTIFY_TAB = "your-spotify" as const;

type RequestState = "idle" | "requesting" | "requested" | "error";
type ActiveTab = SearchSourceName | typeof YOUR_SPOTIFY_TAB;

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [activeTab, setActiveTab] = useState<ActiveTab>("apple");
  const [requestStates, setRequestStates] = useState<Record<string, RequestState>>({});
  const queryClient = useQueryClient();

  const searchQuery = useQuery({
    queryKey: ["search", submittedQuery],
    queryFn: () => api.search.query(submittedQuery),
    enabled: submittedQuery.length > 0,
  });

  const requestMutation = useMutation({
    mutationFn: (track: TrackResult) =>
      api.requests.create({
        source: track.source,
        source_id: track.source_id,
        title: track.title,
        artist: track.artist,
        album: track.album,
        duration_seconds: track.duration_seconds,
        artwork_url: track.artwork_url,
        musicbrainz_recording_id: track.musicbrainz_recording_id,
      }),
  });

  function trackKey(track: TrackResult): string {
    return `${track.source}:${track.source_id}`;
  }

  async function handleRequest(track: TrackResult) {
    const key = trackKey(track);
    setRequestStates((prev) => ({ ...prev, [key]: "requesting" }));
    try {
      await requestMutation.mutateAsync(track);
      setRequestStates((prev) => ({ ...prev, [key]: "requested" }));
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
    } catch {
      setRequestStates((prev) => ({ ...prev, [key]: "error" }));
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmittedQuery(query.trim());
  }

  const activeSource =
    activeTab === YOUR_SPOTIFY_TAB
      ? undefined
      : searchQuery.data?.sources.find((s) => s.source === activeTab);

  return (
    <div>
      <div className="page-header">
        <h1>Find a track</h1>
        <p>
          Search runs across four sources at once. Pick the right result, complete with source badge
          and art, and request just that one recording.
        </p>
      </div>

      <form className="search-form" onSubmit={handleSubmit}>
        <input
          type="search"
          placeholder="Track title or artist"
          aria-label="Search for a track"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="button button--primary" disabled={!query.trim()}>
          Search
        </button>
      </form>

      <div className="search-tabs" role="tablist" aria-label="Search sources">
        {SOURCE_TABS.map((tab) => {
          const source = searchQuery.data?.sources.find((s) => s.source === tab.key);
          const count = source?.results.length ?? 0;
          const failed = source && !source.ok;
          return (
            <button
              key={tab.key}
              role="tab"
              aria-selected={activeTab === tab.key}
              className={`search-tabs__tab ${activeTab === tab.key ? "search-tabs__tab--active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
              type="button"
            >
              {tab.label}
              {submittedQuery && searchQuery.isSuccess && (
                <span className={`search-tabs__count ${failed ? "search-tabs__count--error" : ""}`}>
                  {failed ? "—" : count}
                </span>
              )}
            </button>
          );
        })}
        <button
          role="tab"
          aria-selected={activeTab === YOUR_SPOTIFY_TAB}
          className={`search-tabs__tab ${activeTab === YOUR_SPOTIFY_TAB ? "search-tabs__tab--active" : ""}`}
          onClick={() => setActiveTab(YOUR_SPOTIFY_TAB)}
          type="button"
        >
          Your Spotify
        </button>
      </div>

      <div className="card search-results" role="tabpanel">
        {activeTab === YOUR_SPOTIFY_TAB ? (
          <SpotifyPersonalizationPanel onRequest={handleRequest} requestStates={requestStates} />
        ) : !submittedQuery ? (
          <div className="empty-state">Search for a track to see results here.</div>
        ) : (
          <>
            {searchQuery.isLoading && <p>Searching every source…</p>}
            {searchQuery.isError && (
              <p className="form-error">
                {searchQuery.error instanceof ApiError
                  ? searchQuery.error.message
                  : "Search failed. Try again."}
              </p>
            )}
            {activeSource && !activeSource.ok && (
              <p className="form-error">
                {SOURCE_TABS.find((t) => t.key === activeTab)?.label} is unavailable right now:{" "}
                {activeSource.error}. Other sources are unaffected.
              </p>
            )}
            {activeSource && activeSource.ok && activeSource.results.length === 0 && (
              <div className="empty-state">No matches from this source.</div>
            )}
            {activeSource && activeSource.ok && activeSource.results.length > 0 && (
              <ul className="track-list">
                {activeSource.results.map((track) => (
                  <TrackCard
                    key={trackKey(track)}
                    track={track}
                    onRequest={handleRequest}
                    requestState={requestStates[trackKey(track)] ?? "idle"}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}
