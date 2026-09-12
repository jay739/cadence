import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { PreferredFormat, SearchSourceName, TrackResult } from "../types/api";
import { TrackCard } from "../components/TrackCard";
import { SpotifyPersonalizationPanel } from "../components/SpotifyPersonalizationPanel";
import { useSearchStream } from "../hooks/useSearchStream";
import { useLibraryStatus } from "../hooks/useLibraryStatus";
import "./SearchPage.css";

// Stable identity for "no tracks to look up", so the library query key does not churn while a
// source is still streaming in.
const EMPTY_RESULTS: TrackResult[] = [];

const SOURCE_TABS: { key: SearchSourceName; label: string }[] = [
  { key: "apple", label: "Apple" },
  { key: "musicbrainz", label: "MusicBrainz" },
  { key: "spotify", label: "Spotify" },
  { key: "prowlarr", label: "Prowlarr" },
];

type RequestState = "idle" | "requesting" | "requested" | "error";
type ActiveTab = SearchSourceName;

function trackKey(track: TrackResult): string {
  return `${track.source}:${track.source_id}`;
}

interface SearchResultsPanelProps {
  query: string;
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  onRequest: (track: TrackResult, preferredFormat: PreferredFormat) => Promise<void>;
  requestStates: Record<string, RequestState>;
}

/**
 * Remounted (via a `key={query}` on the parent's usage below) every time a new search is
 * submitted, so useSearchStream's per-query state starts fresh with no manual reset logic:
 * see that hook's docstring for why this project's stricter react-hooks lint rules rule out
 * every alternative short of a remount.
 */
function SearchResultsPanel({
  query,
  activeTab,
  setActiveTab,
  onRequest,
  requestStates,
}: SearchResultsPanelProps) {
  const searchStream = useSearchStream(query);
  const activeSource = searchStream.sources[activeTab];
  // Which of these results the user already owns. Empty until (and unless) Navidrome answers,
  // so nothing here needs a loading or error branch — see the hook's docstring.
  const libraryStatus = useLibraryStatus(activeSource?.results ?? EMPTY_RESULTS);
  const activeSourcePending = !activeSource && query.length > 0;
  const activeLabel = SOURCE_TABS.find((t) => t.key === activeTab)?.label;
  // One Spotify tab: with a query it behaves like any other search tab; with no query (or when
  // Spotify search isn't set up server-side) it shows the signed-in user's own connection —
  // their saved tracks/playlists once connected, or the Connect prompt.
  const spotifyNotSetUp =
    activeTab === "spotify" &&
    activeSource &&
    !activeSource.ok &&
    /not configured/i.test(activeSource.error ?? "");
  const showSpotifyPersonal = activeTab === "spotify" && (!query || spotifyNotSetUp);

  return (
    <>
      <div className="search-tabs" role="tablist" aria-label="Search sources">
        {SOURCE_TABS.map((tab) => {
          const source = searchStream.sources[tab.key];
          const count = source?.results.length ?? 0;
          const failed = source && !source.ok;
          const pending = query.length > 0 && !source;
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
              {query && (
                <span className={`search-tabs__count ${failed ? "search-tabs__count--error" : ""}`}>
                  {pending ? "…" : failed ? "—" : count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="card search-results" role="tabpanel">
        {showSpotifyPersonal ? (
          <SpotifyPersonalizationPanel onRequest={onRequest} requestStates={requestStates} />
        ) : !query ? (
          <div className="empty-state">Start typing above to see results here.</div>
        ) : (
          <>
            {searchStream.isError && (
              <p className="form-error">Search couldn't connect. Refresh the page and try again.</p>
            )}
            {activeSourcePending && (
              <p>
                {searchStream.isLoading
                  ? `Still searching ${activeLabel}…`
                  : `${activeLabel} didn't respond.`}
              </p>
            )}
            {activeSource && !activeSource.ok && (
              <p className="form-error">
                {activeLabel} couldn't be searched: {activeSource.error}. The other tabs still work.
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
                    onRequest={onRequest}
                    requestState={requestStates[trackKey(track)] ?? "idle"}
                    libraryMatch={libraryStatus[trackKey(track)]}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </>
  );
}

interface ResolveLinkPanelProps {
  onRequest: (track: TrackResult, preferredFormat: PreferredFormat) => Promise<void>;
  requestStates: Record<string, RequestState>;
}

/**
 * Lets a user paste a direct Spotify or Apple Music track link and resolve it to one exact
 * track, as an alternative to the free-text search above for someone who already knows
 * precisely which recording they want. Only these two platforms are supported today (see
 * backend/app/services/link_resolve.py for why YouTube links are out of scope).
 *
 * Deliberately separate from useSearchStream's per-query state: a resolved link is a single
 * one-shot lookup, not a stream of per-source results, so a plain useMutation plus local state
 * covers it without the remount trick the search results panel needs.
 */
function ResolveLinkPanel({ onRequest, requestStates }: ResolveLinkPanelProps) {
  const [linkUrl, setLinkUrl] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);
  const [resolvedTrack, setResolvedTrack] = useState<TrackResult | null>(null);
  // Someone who pasted an exact link is the likeliest person to already own the track, so this
  // panel gets the same library badge the search results do.
  const resolvedTracks = useMemo(
    () => (resolvedTrack ? [resolvedTrack] : EMPTY_RESULTS),
    [resolvedTrack],
  );
  const libraryStatus = useLibraryStatus(resolvedTracks);

  const resolveLinkMutation = useMutation({
    mutationFn: (url: string) => api.search.resolveLink(url),
  });

  async function handleResolveLink(event: FormEvent) {
    event.preventDefault();
    const trimmed = linkUrl.trim();
    if (!trimmed) return;
    setLinkError(null);
    try {
      const track = await resolveLinkMutation.mutateAsync(trimmed);
      setResolvedTrack(track);
    } catch (err) {
      setResolvedTrack(null);
      setLinkError(
        err instanceof ApiError ? err.message : "Couldn't resolve that link. Try again.",
      );
    }
  }

  return (
    <div className="link-resolve">
      <form className="link-resolve-form" onSubmit={handleResolveLink}>
        <input
          type="url"
          placeholder="…or paste a Spotify or Apple Music track link"
          aria-label="Paste a Spotify or Apple Music track link"
          value={linkUrl}
          onChange={(e) => {
            setLinkUrl(e.target.value);
            setLinkError(null);
          }}
        />
        <button
          type="submit"
          className="button button--ghost"
          disabled={!linkUrl.trim() || resolveLinkMutation.isPending}
        >
          {resolveLinkMutation.isPending ? "Resolving…" : "Resolve link"}
        </button>
      </form>
      {linkError && <p className="form-error">{linkError}</p>}
      {resolvedTrack && (
        <ul className="track-list">
          <TrackCard
            key={trackKey(resolvedTrack)}
            track={resolvedTrack}
            onRequest={onRequest}
            requestState={requestStates[trackKey(resolvedTrack)] ?? "idle"}
            libraryMatch={libraryStatus[trackKey(resolvedTrack)]}
          />
        </ul>
      )}
    </div>
  );
}

// As-you-type tuning: long enough that a fast typist doesn't fire a stream per keystroke,
// short enough that results feel immediate once they pause; 2-char minimum keeps one-letter
// junk queries from fanning out to four real APIs.
const SEARCH_DEBOUNCE_MS = 400;
const MIN_SEARCH_CHARS = 2;

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [activeTab, setActiveTab] = useState<ActiveTab>("apple");
  const [requestStates, setRequestStates] = useState<Record<string, RequestState>>({});
  const queryClient = useQueryClient();
  const debounceTimer = useRef<number | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Cleanup-only effect: a pending debounce timer must not fire after unmount. All setState
  // calls live in event handlers and the timer callback, never in an effect body, per this
  // project's strict react-hooks v7 lint rules.
  useEffect(() => {
    return () => {
      if (debounceTimer.current !== null) {
        window.clearTimeout(debounceTimer.current);
      }
    };
  }, []);

  // "/" focuses search from anywhere on the page, the same convention GitHub/Slack/etc. use,
  // skipped while the user is already typing in any input/textarea (including this one) so a
  // literal "/" in a query or note is never intercepted.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "/") return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      event.preventDefault();
      searchInputRef.current?.focus();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const requestMutation = useMutation({
    mutationFn: ({
      track,
      preferredFormat,
    }: {
      track: TrackResult;
      preferredFormat: PreferredFormat;
    }) =>
      api.requests.create({
        source: track.source,
        source_id: track.source_id,
        title: track.title,
        artist: track.artist,
        album: track.album,
        duration_seconds: track.duration_seconds,
        artwork_url: track.artwork_url,
        musicbrainz_recording_id: track.musicbrainz_recording_id,
        preferred_format: preferredFormat,
      }),
  });

  async function handleRequest(track: TrackResult, preferredFormat: PreferredFormat) {
    const key = trackKey(track);
    setRequestStates((prev) => ({ ...prev, [key]: "requesting" }));
    try {
      await requestMutation.mutateAsync({ track, preferredFormat });
      setRequestStates((prev) => ({ ...prev, [key]: "requested" }));
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
    } catch {
      setRequestStates((prev) => ({ ...prev, [key]: "error" }));
    }
  }

  function handleQueryChange(next: string) {
    setQuery(next);
    if (debounceTimer.current !== null) {
      window.clearTimeout(debounceTimer.current);
    }
    const trimmed = next.trim();
    debounceTimer.current = window.setTimeout(() => {
      setActiveQuery(trimmed.length >= MIN_SEARCH_CHARS ? trimmed : "");
    }, SEARCH_DEBOUNCE_MS);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    // Instant flush: pressing Enter or the button skips the debounce wait, and also allows a
    // deliberate one-character search the as-you-type minimum would otherwise hold back.
    if (debounceTimer.current !== null) {
      window.clearTimeout(debounceTimer.current);
    }
    setActiveQuery(query.trim());
  }

  return (
    <div>
      <div className="page-header">
        <h1>Find a track</h1>
        <p>
          Results appear as you type, across four sources at once. Pick the right result, complete
          with source badge and art, and request just that one recording.
        </p>
      </div>

      <form className="search-form" onSubmit={handleSubmit}>
        <input
          ref={searchInputRef}
          type="search"
          placeholder="Start typing a track title or artist… (press / to focus)"
          aria-label="Search for a track"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
        />
        <button type="submit" className="button button--primary" disabled={!query.trim()}>
          Search
        </button>
      </form>

      <ResolveLinkPanel onRequest={handleRequest} requestStates={requestStates} />

      <SearchResultsPanel
        key={activeQuery}
        query={activeQuery}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onRequest={handleRequest}
        requestStates={requestStates}
      />
    </div>
  );
}
