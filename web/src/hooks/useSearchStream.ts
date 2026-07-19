import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { SourceSearchResult } from "../types/api";

interface SearchStreamState {
  sources: Record<string, SourceSearchResult>;
  isLoading: boolean;
  isDone: boolean;
  isError: boolean;
}

/**
 * Consumes GET /api/search/stream via the browser's native EventSource API: each source's
 * SourceSearchResult arrives the moment that source finishes on the backend, rather than
 * waiting for the slowest of the four (see app/services/search.py's stream_source_results for
 * why: MusicBrainz is hard rate-limited to 1 request/second, and a slow/misconfigured Prowlarr
 * can otherwise make every tab wait on it). `sources` fills in incrementally as events arrive;
 * a consumer reads sources[tabKey] the same way it previously read the single batched
 * response's `.sources.find(...)`, it just may be undefined for a tab whose source hasn't
 * finished yet, which is the whole point.
 *
 * IMPORTANT: the caller must remount this hook's component on every new query, e.g.
 * `<SearchResults key={submittedQuery} query={submittedQuery} />`, rather than relying on this
 * hook to reset its own state when `query` changes. This project's react-hooks lint config
 * (v7, the React Compiler-oriented ruleset) forbids both an unconditional setState in an
 * effect body and a ref read/write during render, which rules out every "detect the prop
 * changed, reset state" pattern short of the key-remount approach React's own docs now
 * recommend for exactly this case. A fresh mount means useState's initial value already
 * reflects the current query with no reset logic needed at all.
 */
export function useSearchStream(query: string): SearchStreamState {
  const [state, setState] = useState<SearchStreamState>({
    sources: {},
    isLoading: query.length > 0,
    isDone: false,
    isError: false,
  });
  const isDoneRef = useRef(false);

  useEffect(() => {
    if (!query) {
      return;
    }

    const eventSource = new EventSource(api.search.streamUrl(query), {
      withCredentials: true,
    });

    eventSource.onmessage = (event) => {
      const result = JSON.parse(event.data) as SourceSearchResult;
      setState((prev) => ({ ...prev, sources: { ...prev.sources, [result.source]: result } }));
    };

    eventSource.addEventListener("done", () => {
      isDoneRef.current = true;
      setState((prev) => ({ ...prev, isLoading: false, isDone: true }));
      eventSource.close();
    });

    eventSource.onerror = () => {
      // EventSource's error event fires both for a genuine connection failure and, per spec,
      // for the connection simply ending; the "done" listener above already sets isDone on the
      // success path, so only treat this as a real error if that never happened first.
      setState((prev) => ({
        ...prev,
        isLoading: false,
        isError: prev.isError || !isDoneRef.current,
      }));
      eventSource.close();
    };

    return () => {
      eventSource.close();
    };
  }, [query]);

  return state;
}
