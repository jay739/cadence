import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TrackCard } from "./TrackCard";
import type { LibraryMatch, TrackResult } from "../types/api";

const baseTrack: TrackResult = {
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

describe("TrackCard", () => {
  it("renders title, artist, source badge, and duration", () => {
    render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="idle" />);
    expect(screen.getByText("Tum Hi Ho")).toBeInTheDocument();
    expect(screen.getByText("Arijit Singh")).toBeInTheDocument();
    expect(screen.getByText("Apple")).toBeInTheDocument();
    expect(screen.getByText("4:22")).toBeInTheDocument();
  });

  it("renders Prowlarr-specific metadata (indexer, size, seeders) when present", () => {
    const prowlarrTrack: TrackResult = {
      ...baseTrack,
      source: "prowlarr",
      indexer: "SomeIndexer",
      size_bytes: 30_000_000,
      seeders: 12,
    };
    render(<TrackCard track={prowlarrTrack} onRequest={vi.fn()} requestState="idle" />);
    expect(screen.getByText("SomeIndexer")).toBeInTheDocument();
    expect(screen.getByText("28.6 MB")).toBeInTheDocument();
    expect(screen.getByText("12 seeders")).toBeInTheDocument();
  });

  it("calls onRequest with the track and the default Any quality when clicked", async () => {
    const onRequest = vi.fn();
    const user = userEvent.setup();
    render(<TrackCard track={baseTrack} onRequest={onRequest} requestState="idle" />);
    await user.click(screen.getByRole("button", { name: /request/i }));
    expect(onRequest).toHaveBeenCalledWith(baseTrack, "any");
  });

  it("passes the chosen quality preference through to onRequest", async () => {
    const onRequest = vi.fn();
    const user = userEvent.setup();
    render(<TrackCard track={baseTrack} onRequest={onRequest} requestState="idle" />);
    await user.selectOptions(screen.getByRole("combobox", { name: /preferred quality/i }), "flac");
    await user.click(screen.getByRole("button", { name: /request/i }));
    expect(onRequest).toHaveBeenCalledWith(baseTrack, "flac");
  });

  it("disables the button and shows progress while requesting", () => {
    render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="requesting" />);
    const button = screen.getByRole("button", { name: /requesting/i });
    expect(button).toBeDisabled();
  });

  it("disables the button and shows requested state once done", () => {
    render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="requested" />);
    const button = screen.getByRole("button", { name: /^requested$/i });
    expect(button).toBeDisabled();
  });

  it("shows a retry affordance on error and remains clickable", () => {
    render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="error" />);
    const button = screen.getByRole("button", { name: /try again/i });
    expect(button).not.toBeDisabled();
  });

  describe("already-in-library badge", () => {
    const match: LibraryMatch = {
      key: "apple:track-1",
      navidrome_id: "nd-1",
      title: "Tum Hi Ho",
      artist: "Arijit Singh",
      album: "Aashiqui 2",
    };

    it("shows the badge when the track was matched in the library", () => {
      render(
        <TrackCard
          track={baseTrack}
          onRequest={vi.fn()}
          requestState="idle"
          libraryMatch={match}
        />,
      );
      expect(screen.getByText(/in your library/i)).toBeInTheDocument();
    });

    it("names what was matched, so the claim can be checked", () => {
      render(
        <TrackCard
          track={baseTrack}
          onRequest={vi.fn()}
          requestState="idle"
          libraryMatch={match}
        />,
      );
      expect(screen.getByText(/in your library/i)).toHaveAttribute(
        "title",
        'Matched "Tum Hi Ho" by Arijit Singh on Aashiqui 2',
      );
    });

    it("shows no badge when there is no match", () => {
      render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="idle" />);
      expect(screen.queryByText(/in your library/i)).not.toBeInTheDocument();
    });

    it("shows no badge when the lookup returned nothing for this track", () => {
      // undefined is what the lookup table yields both for a genuine miss and for a failed or
      // unconfigured check. Silence is correct in every one of those cases.
      render(
        <TrackCard
          track={baseTrack}
          onRequest={vi.fn()}
          requestState="idle"
          libraryMatch={undefined}
        />,
      );
      expect(screen.queryByText(/in your library/i)).not.toBeInTheDocument();
    });

    it("still lets the track be requested, since the badge is advice not a block", () => {
      render(
        <TrackCard
          track={baseTrack}
          onRequest={vi.fn()}
          requestState="idle"
          libraryMatch={match}
        />,
      );
      expect(screen.getByRole("button", { name: /request/i })).not.toBeDisabled();
    });
  });
});
