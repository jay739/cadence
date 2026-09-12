import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLibraryStatus } from "./useLibraryStatus";
import { api } from "../api/client";
import type { LibraryCheckResponse, TrackResult } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { ...actual.api, library: { check: vi.fn() } } };
});

const mockedCheck = vi.mocked(api.library.check);

const track: TrackResult = {
  source: "apple",
  source_id: "track-1",
  title: "Tum Hi Ho",
  artist: "Arijit Singh",
  album: "Aashiqui 2",
  duration_seconds: 262,
  artwork_url: null,
  year: 2013,
  musicbrainz_recording_id: null,
  indexer: null,
  size_bytes: null,
  seeders: null,
  download_url: null,
  guid: null,
};

const otherTrack: TrackResult = { ...track, source_id: "track-2", title: "Another Song" };

function wrapper({ children }: { children: ReactNode }) {
  // retry:false and a fresh client per test so a rejected query settles immediately rather
  // than being retried into a timeout.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function response(partial: Partial<LibraryCheckResponse> = {}): LibraryCheckResponse {
  return { available: true, matches: [], ...partial };
}

describe("useLibraryStatus", () => {
  beforeEach(() => {
    mockedCheck.mockReset();
  });

  it("maps matches by track key", async () => {
    mockedCheck.mockResolvedValue(
      response({
        matches: [
          {
            key: "apple:track-1",
            navidrome_id: "nd-1",
            title: "Tum Hi Ho",
            artist: "Arijit Singh",
            album: "Aashiqui 2",
          },
        ],
      }),
    );

    const { result } = renderHook(() => useLibraryStatus([track]), { wrapper });

    await waitFor(() => expect(result.current["apple:track-1"]).toBeDefined());
    expect(result.current["apple:track-1"].navidrome_id).toBe("nd-1");
  });

  it("sends one item per track, keyed source:source_id", async () => {
    mockedCheck.mockResolvedValue(response());

    renderHook(() => useLibraryStatus([track, otherTrack]), { wrapper });

    await waitFor(() => expect(mockedCheck).toHaveBeenCalled());
    expect(mockedCheck).toHaveBeenCalledWith([
      { key: "apple:track-1", title: "Tum Hi Ho", artist: "Arijit Singh" },
      { key: "apple:track-2", title: "Another Song", artist: "Arijit Singh" },
    ]);
  });

  it("reports nothing when the check is unavailable, rather than 'not in library'", async () => {
    // available:false means unknown — an unconfigured or unreachable Navidrome. Any match list
    // that arrives alongside it must be ignored outright.
    mockedCheck.mockResolvedValue(
      response({
        available: false,
        matches: [
          {
            key: "apple:track-1",
            navidrome_id: "nd-1",
            title: "Tum Hi Ho",
            artist: "Arijit Singh",
            album: null,
          },
        ],
      }),
    );

    const { result } = renderHook(() => useLibraryStatus([track]), { wrapper });

    await waitFor(() => expect(mockedCheck).toHaveBeenCalled());
    expect(result.current).toEqual({});
  });

  it("stays empty when the request fails outright", async () => {
    mockedCheck.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useLibraryStatus([track]), { wrapper });

    await waitFor(() => expect(mockedCheck).toHaveBeenCalled());
    expect(result.current).toEqual({});
  });

  it("does not call the API at all when there are no tracks", () => {
    const { result } = renderHook(() => useLibraryStatus([]), { wrapper });

    expect(mockedCheck).not.toHaveBeenCalled();
    expect(result.current).toEqual({});
  });

  it("does not refetch when re-rendered with an equivalent track list", async () => {
    mockedCheck.mockResolvedValue(response());

    const { rerender } = renderHook(({ tracks }) => useLibraryStatus(tracks), {
      wrapper,
      initialProps: { tracks: [track] },
    });
    await waitFor(() => expect(mockedCheck).toHaveBeenCalledTimes(1));

    // A new array containing an equal track: the query key is derived from the track keys, not
    // from the array's identity, so this must not fire a second lookup.
    rerender({ tracks: [{ ...track }] });

    expect(mockedCheck).toHaveBeenCalledTimes(1);
  });
});
