import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LibraryMatch, TrackResult } from "../types/api";

/**
 * Looks up which of `tracks` are already in the Navidrome library, keyed by `source:source_id`.
 *
 * Designed to be impossible to notice when it goes wrong. There is no loading state, no error
 * state and no thrown exception in the return type: the caller gets a lookup table that is
 * simply empty until an answer arrives, and stays empty forever if Navidrome is unconfigured,
 * unreachable, or answers something unusable. A track missing from the table renders no badge,
 * which is exactly what should happen when the answer is unknown, because claiming "not in your
 * library" on a failed lookup would be as wrong as a false positive.
 *
 * One request per distinct set of tracks, cached by react-query for the session. The search
 * results panel is remounted per query (see useSearchStream), and each source tab has its own
 * result set, so this naturally fires once per tab per query rather than per render.
 */
export function useLibraryStatus(tracks: TrackResult[]): Record<string, LibraryMatch> {
  // The query key is the set of tracks being asked about. Deriving it from the track keys
  // (rather than from the array's identity) means an unchanged result set re-renders without
  // refetching, and a genuinely different one gets its own cache entry.
  const cacheKey = tracks.map((track) => `${track.source}:${track.source_id}`).join(",");

  const { data } = useQuery({
    queryKey: ["library-check", cacheKey],
    queryFn: () =>
      api.library.check(
        tracks.map((track) => ({
          key: `${track.source}:${track.source_id}`,
          title: track.title,
          artist: track.artist,
        })),
      ),
    enabled: tracks.length > 0,
    // A decoration on search results: not worth retrying, and not worth re-asking about the
    // same tracks for a while. The library only changes when a download completes.
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  return useMemo(() => {
    if (!data?.available) return {};
    return Object.fromEntries(data.matches.map((match) => [match.key, match]));
  }, [data]);
}
