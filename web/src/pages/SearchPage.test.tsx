import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SearchPage } from "./SearchPage";
import { api, ApiError } from "../api/client";
import type { TrackResult } from "../types/api";

// Only `search.resolveLink` is mocked; `search.query`/`streamUrl` keep their real
// implementations since useSearchStream builds its EventSource URL from the real streamUrl().
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      search: {
        ...actual.api.search,
        resolveLink: vi.fn(),
      },
    },
  };
});

const mockedApi = api as unknown as {
  search: { resolveLink: ReturnType<typeof vi.fn> };
};

/**
 * jsdom does not implement EventSource, so the streaming search is exercised against this
 * recording stub: enough surface for useSearchStream (onmessage/onerror/addEventListener/
 * close), plus instance bookkeeping so tests can assert when streams open, with what URL,
 * and that superseded ones are closed.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  withCredentials: boolean;
  closed = false;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Record<string, Array<() => void>> = {};

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = init?.withCredentials ?? false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, callback: () => void) {
    (this.listeners[type] ??= []).push(callback);
  }

  close() {
    this.closed = true;
  }
}

vi.stubGlobal("EventSource", FakeEventSource);

function renderSearchPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SearchPage />
    </QueryClientProvider>,
  );
}

function typeQuery(text: string) {
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: text } });
}

describe("SearchPage as-you-type", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not open a stream while typing, only after the debounce settles", () => {
    renderSearchPage();
    typeQuery("tum hi");
    expect(FakeEventSource.instances).toHaveLength(0);

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=tum+hi");
  });

  it("never fires for a single character", () => {
    renderSearchPage();
    typeQuery("t");
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("a newer query closes the superseded stream and opens a fresh one", () => {
    renderSearchPage();
    typeQuery("tum");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    typeQuery("tum hi ho");
    act(() => {
      vi.advanceTimersByTime(400);
    });

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[0].closed).toBe(true);
    expect(FakeEventSource.instances[1].url).toContain("q=tum+hi+ho");
    expect(FakeEventSource.instances[1].closed).toBe(false);
  });

  it("keystrokes inside the debounce window collapse into one stream", () => {
    renderSearchPage();
    typeQuery("tu");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    typeQuery("tum");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    typeQuery("tum h");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=tum+h");
  });

  it("submitting flushes immediately without waiting for the debounce", () => {
    renderSearchPage();
    typeQuery("arijit");
    expect(FakeEventSource.instances).toHaveLength(0);

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Search" }));
    });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("q=arijit");
  });
});

describe("SearchPage '/' shortcut", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
  });

  it("focuses the search box when '/' is pressed anywhere on the page", () => {
    renderSearchPage();
    const searchbox = screen.getByRole("searchbox");
    expect(searchbox).not.toHaveFocus();

    fireEvent.keyDown(document.body, { key: "/" });

    expect(searchbox).toHaveFocus();
  });

  it("does not intercept '/' typed inside the search box itself", () => {
    renderSearchPage();
    const searchbox = screen.getByRole("searchbox");
    searchbox.focus();

    fireEvent.keyDown(searchbox, { key: "/" });
    typeQuery("ac/dc");

    expect(searchbox).toHaveValue("ac/dc");
  });
});

const resolvedTrack: TrackResult = {
  source: "spotify",
  source_id: "6y0igZArWVi6Iz0rj35c1Y",
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

const SPOTIFY_LINK = "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y";

/**
 * The one search box has to pick a behaviour from what's in it: resolve a link, or search for
 * words. These cover both the branch itself and the thing that makes the merge safe: a link is
 * never looked up as-you-type, because every prefix of a URL is an unrecognized link and would
 * earn the user one 422 per keystroke.
 */
describe("SearchPage unified box: link vs free text", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    mockedApi.search.resolveLink.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function pasteQuery(text: string) {
    const box = screen.getByRole("searchbox");
    fireEvent.paste(box);
    fireEvent.change(box, { target: { value: text } });
  }

  it("resolves a pasted link on its own and renders it with the existing TrackCard", async () => {
    mockedApi.search.resolveLink.mockResolvedValue(resolvedTrack);
    renderSearchPage();

    // No submit: a paste is a complete link the moment it lands, so it settles into a lookup.
    pasteQuery(SPOTIFY_LINK);

    expect(await screen.findByText("Tum Hi Ho")).toBeInTheDocument();
    expect(screen.getByText("Arijit Singh")).toBeInTheDocument();
    expect(mockedApi.search.resolveLink).toHaveBeenCalledWith(SPOTIFY_LINK);
    // A URL is never fanned out to the four search sources as if it were a song title.
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("never looks a link up mid-typing, only on submit", async () => {
    vi.useFakeTimers();
    mockedApi.search.resolveLink.mockResolvedValue(resolvedTrack);
    renderSearchPage();

    // Typed by hand, one keystroke at a time, waiting out the debounce after every one.
    for (const prefix of [
      "h",
      "ht",
      "https://",
      "https://open.spotify.com/tr",
      "https://open.spotify.com/track/6y0",
      SPOTIFY_LINK,
    ]) {
      typeQuery(prefix);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    }

    expect(mockedApi.search.resolveLink).not.toHaveBeenCalled();
    // …and the half-typed URL was never sent to the search sources either.
    expect(FakeEventSource.instances.some((s) => s.url.includes("spotify.com"))).toBe(false);
    // The one button says which of the two things it is about to do.
    expect(screen.getByRole("button", { name: "Resolve link" })).toBeInTheDocument();
  });

  it("submits a hand-typed link as a lookup, not as a search", async () => {
    mockedApi.search.resolveLink.mockResolvedValue(resolvedTrack);
    renderSearchPage();

    typeQuery(SPOTIFY_LINK);
    fireEvent.click(screen.getByRole("button", { name: "Resolve link" }));

    expect(await screen.findByText("Tum Hi Ho")).toBeInTheDocument();
    expect(mockedApi.search.resolveLink).toHaveBeenCalledTimes(1);
    expect(mockedApi.search.resolveLink).toHaveBeenCalledWith(SPOTIFY_LINK);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("adds the scheme a mobile share sheet leaves off a known host", async () => {
    mockedApi.search.resolveLink.mockResolvedValue(resolvedTrack);
    renderSearchPage();

    pasteQuery("open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y");

    // Without this the backend would reject a perfectly good link for having no scheme, with a
    // message about the wrong platform.
    await screen.findByText("Tum Hi Ho");
    expect(mockedApi.search.resolveLink).toHaveBeenCalledWith(SPOTIFY_LINK);
  });

  it("shows the backend's specific error message inline, not a toast or a generic message", async () => {
    mockedApi.search.resolveLink.mockRejectedValue(
      new ApiError(
        422,
        { detail: "That doesn't look like a Spotify or Apple Music track link" },
        "That doesn't look like a Spotify or Apple Music track link",
      ),
    );
    renderSearchPage();

    // A link to a platform the backend doesn't resolve still goes to the resolver, so the user
    // reads why, instead of watching four search sources find nothing for a URL.
    pasteQuery("https://youtube.com/watch?v=dQw4w9WgXcQ");

    expect(
      await screen.findByText("That doesn't look like a Spotify or Apple Music track link"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Tum Hi Ho")).not.toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("shows a specific 404 message when the platform is recognized but the track isn't found", async () => {
    mockedApi.search.resolveLink.mockRejectedValue(
      new ApiError(
        404,
        { detail: "Couldn't find that track on Spotify" },
        "Couldn't find that track on Spotify",
      ),
    );
    renderSearchPage();

    pasteQuery("https://open.spotify.com/track/0000000000000000000000");

    expect(await screen.findByText("Couldn't find that track on Spotify")).toBeInTheDocument();
  });

  it("clears a previous error once the box is edited", async () => {
    mockedApi.search.resolveLink.mockRejectedValue(
      new ApiError(422, { detail: "Bad link" }, "Bad link"),
    );
    renderSearchPage();

    pasteQuery("https://example.com/not-a-track");
    expect(await screen.findByText("Bad link")).toBeInTheDocument();

    typeQuery(SPOTIFY_LINK);
    expect(screen.queryByText("Bad link")).not.toBeInTheDocument();
  });

  it("stands the source tabs down for a link and brings them back for words", async () => {
    mockedApi.search.resolveLink.mockResolvedValue(resolvedTrack);
    renderSearchPage();

    expect(screen.getByRole("tablist")).toBeInTheDocument();

    pasteQuery(SPOTIFY_LINK);
    await screen.findByText("Tum Hi Ho");
    // The four per-source tabs have nothing to say about one exact link.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();

    typeQuery("tum hi ho");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
  });
});
