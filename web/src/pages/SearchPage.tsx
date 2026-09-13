import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { DesiredQuality, SearchSourceName, TrackResult } from "../types/api";
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
  onRequest: (track: TrackResult, desiredQuality: DesiredQuality) => Promise<void>;
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

interface LinkResultPanelProps {
  track: TrackResult | null;
  error: string | null;
  isResolving: boolean;
  onRequest: (track: TrackResult, desiredQuality: DesiredQuality) => Promise<void>;
  requestStates: Record<string, RequestState>;
}

/**
 * What the results area shows while the one search box holds a link rather than words: the
 * single exact track that link resolves to, for someone who already knows precisely which
 * recording they want. The four per-source tabs have nothing useful to say about an exact
 * link, so they stand down for this mode instead of streaming a nonsense query.
 *
 * Only Spotify and Apple Music links resolve (see backend/app/services/link_resolve.py for why
 * YouTube links are out of scope). A link from anywhere else still lands here rather than in
 * the search stream, so the backend's specific "that isn't a Spotify or Apple Music track
 * link" message is what the user reads, inline, right where they pasted it.
 *
 * Deliberately outside useSearchStream's per-query state: a resolved link is a single one-shot
 * lookup, not a stream of per-source results, so plain state in the page covers it without the
 * remount trick the search results panel needs.
 */
function LinkResultPanel({
  track,
  error,
  isResolving,
  onRequest,
  requestStates,
}: LinkResultPanelProps) {
  // Someone who pasted an exact link is the likeliest person to already own the track, so a
  // resolved link gets the same library badge the search results do.
  const resolvedTracks = useMemo(() => (track ? [track] : EMPTY_RESULTS), [track]);
  const libraryStatus = useLibraryStatus(resolvedTracks);

  return (
    <div className="card search-results">
      {error && <p className="form-error">{error}</p>}
      {isResolving && <p>Looking up that link…</p>}
      {!error && !isResolving && !track && (
        <div className="empty-state">That looks like a track link. Press Enter to look it up.</div>
      )}
      {track && (
        <ul className="track-list">
          <TrackCard
            key={trackKey(track)}
            track={track}
            onRequest={onRequest}
            requestState={requestStates[trackKey(track)] ?? "idle"}
            libraryMatch={libraryStatus[trackKey(track)]}
          />
        </ul>
      )}
    </div>
  );
}

// The hosts the backend can actually resolve today (backend/app/services/link_resolve.py:
// Spotify and Apple Music, YouTube deliberately deferred). Used only to recognize a
// scheme-less paste; the backend re-parses and re-validates every URL it is given.
const TRACK_LINK_HOSTS = ["open.spotify.com", "music.apple.com"];

/**
 * Decides which of the one search box's two behaviours the current input gets: resolve a link,
 * or search for words. Deliberately blunt.
 *
 * Anything explicitly http(s) counts, whatever the host, because a YouTube or Bandcamp link is
 * a link, and sending it to the resolver earns the backend's specific "that doesn't look like
 * a Spotify or Apple Music track link" rejection, which is far more useful than fanning a URL
 * out to four search APIs as if it were a song title. A scheme-less paste of a host the
 * backend knows counts too, since that is what several mobile share sheets hand over.
 */
function looksLikeTrackLink(value: string): boolean {
  const candidate = value.trim().toLowerCase();
  if (candidate.startsWith("http://") || candidate.startsWith("https://")) return true;
  return TRACK_LINK_HOSTS.some((host) => candidate.startsWith(`${host}/`));
}

/**
 * The backend only accepts an http(s) URL (parse_track_link's scheme check), so a scheme-less
 * paste gets the scheme added here. Otherwise a perfectly good `open.spotify.com/track/…`
 * would come back rejected as "not a Spotify link", which is both wrong and unactionable.
 */
function normalizeTrackLink(value: string): string {
  const trimmed = value.trim();
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

// As-you-type tuning: long enough that a fast typist doesn't fire a stream per keystroke,
// short enough that results feel immediate once they pause; 2-char minimum keeps one-letter
// junk queries from fanning out to four real APIs.
const SEARCH_DEBOUNCE_MS = 400;
const MIN_SEARCH_CHARS = 2;
// A link is never looked up as-you-type: every prefix of a real URL is an unrecognized link,
// so a typing debounce would earn the user one 422 per keystroke. A *paste* arrives whole in a
// single change event, so this short settle is not a typing debounce at all: it exists only so
// that carrying on typing (or pasting again) straight after a paste cancels the lookup rather
// than racing it. Everything else waits for an explicit submit.
const LINK_PASTE_SETTLE_MS = 250;

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [activeTab, setActiveTab] = useState<ActiveTab>("apple");
  const [requestStates, setRequestStates] = useState<Record<string, RequestState>>({});
  const [linkError, setLinkError] = useState<string | null>(null);
  const [resolvedTrack, setResolvedTrack] = useState<TrackResult | null>(null);
  const queryClient = useQueryClient();
  const pendingLookup = useRef<number | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  // Set by the paste handler and consumed by the change handler that immediately follows it,
  // so the change handler can tell "the whole URL just arrived at once" from "the user is
  // typing a URL character by character", since only the former ever auto-resolves.
  const pastedIntoBox = useRef(false);
  // Monotonic id for link lookups: with a paste able to fire one on its own, two can be in
  // flight at once (paste, edit, paste again), and the slower one must not overwrite the newer
  // one's result. Any input change bumps it, which also discards a lookup the user has moved on
  // from.
  const lookupSequence = useRef(0);

  // Cleanup-only effect: a pending debounce/paste-settle timer must not fire after unmount. All
  // setState calls live in event handlers and the timer callback, never in an effect body, per
  // this project's strict react-hooks v7 lint rules.
  useEffect(() => {
    return () => {
      if (pendingLookup.current !== null) {
        window.clearTimeout(pendingLookup.current);
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
      desiredQuality,
    }: {
      track: TrackResult;
      desiredQuality: DesiredQuality;
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
        desired_quality: desiredQuality,
      }),
  });

  async function handleRequest(track: TrackResult, desiredQuality: DesiredQuality) {
    const key = trackKey(track);
    setRequestStates((prev) => ({ ...prev, [key]: "requesting" }));
    try {
      await requestMutation.mutateAsync({ track, desiredQuality });
      setRequestStates((prev) => ({ ...prev, [key]: "requested" }));
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
    } catch {
      setRequestStates((prev) => ({ ...prev, [key]: "error" }));
    }
  }

  const resolveLinkMutation = useMutation({
    mutationFn: (url: string) => api.search.resolveLink(url),
  });

  async function resolveLink(rawUrl: string) {
    const lookupId = ++lookupSequence.current;
    setLinkError(null);
    setResolvedTrack(null);
    try {
      const track = await resolveLinkMutation.mutateAsync(normalizeTrackLink(rawUrl));
      if (lookupSequence.current !== lookupId) return;
      setResolvedTrack(track);
    } catch (err) {
      if (lookupSequence.current !== lookupId) return;
      setResolvedTrack(null);
      // The backend's own wording, verbatim: a 422 names the platform it couldn't recognize and
      // a 404 names the platform it asked, both of which tell the user what to do next. Shown
      // inline under the box they pasted into, never as a toast that outlives the input.
      setLinkError(
        err instanceof ApiError ? err.message : "Couldn't resolve that link. Try again.",
      );
    }
  }

  function handleQueryChange(next: string) {
    const wholeValueJustPasted = pastedIntoBox.current;
    pastedIntoBox.current = false;
    setQuery(next);
    // Anything the user types invalidates the last lookup's result, whether or not it has
    // landed yet.
    lookupSequence.current += 1;
    setLinkError(null);
    setResolvedTrack(null);
    if (pendingLookup.current !== null) {
      window.clearTimeout(pendingLookup.current);
    }

    const trimmed = next.trim();
    if (looksLikeTrackLink(trimmed)) {
      // A URL is never a useful free-text query, so stop the streaming search rather than
      // fanning one out to four real APIs, and drop any results still on screen from before.
      setActiveQuery("");
      if (wholeValueJustPasted) {
        pendingLookup.current = window.setTimeout(() => {
          void resolveLink(trimmed);
        }, LINK_PASTE_SETTLE_MS);
      }
      return;
    }

    pendingLookup.current = window.setTimeout(() => {
      setActiveQuery(trimmed.length >= MIN_SEARCH_CHARS ? trimmed : "");
    }, SEARCH_DEBOUNCE_MS);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    // Instant flush: pressing Enter or the button skips the debounce wait, and also allows a
    // deliberate one-character search the as-you-type minimum would otherwise hold back.
    if (pendingLookup.current !== null) {
      window.clearTimeout(pendingLookup.current);
    }
    const trimmed = query.trim();
    if (looksLikeTrackLink(trimmed)) {
      void resolveLink(trimmed);
      return;
    }
    setActiveQuery(trimmed);
  }

  // One box, two behaviours, decided by what's in it rather than by which of two boxes the user
  // picked: a link resolves to one exact track, anything else searches across the four sources.
  const isLinkMode = looksLikeTrackLink(query);

  return (
    <div>
      <div className="page-header">
        <h1>Find a track</h1>
        <p>
          Results appear as you type, across four sources at once. Pick the right result, complete
          with source badge and art, and request just that one recording. Already know the exact
          recording? Paste its Spotify or Apple Music link into the same box.
        </p>
      </div>

      <form className="search-form" onSubmit={handleSubmit}>
        <input
          ref={searchInputRef}
          type="search"
          placeholder="Track title or artist, or paste a track link… (press / to focus)"
          aria-label="Search for a track, or paste a Spotify or Apple Music link"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          onPaste={() => {
            pastedIntoBox.current = true;
          }}
        />
        <button
          type="submit"
          className="button button--primary"
          disabled={!query.trim() || resolveLinkMutation.isPending}
        >
          {isLinkMode ? (resolveLinkMutation.isPending ? "Resolving…" : "Resolve link") : "Search"}
        </button>
      </form>

      {isLinkMode ? (
        <LinkResultPanel
          track={resolvedTrack}
          error={linkError}
          isResolving={resolveLinkMutation.isPending}
          onRequest={handleRequest}
          requestStates={requestStates}
        />
      ) : (
        <SearchResultsPanel
          key={activeQuery}
          query={activeQuery}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          onRequest={handleRequest}
          requestStates={requestStates}
        />
      )}
    </div>
  );
}
