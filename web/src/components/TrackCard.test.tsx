import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TrackCard } from "./TrackCard";
import type { TrackResult } from "../types/api";

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

  it("passes the chosen quality through to onRequest", async () => {
    const onRequest = vi.fn();
    const user = userEvent.setup();
    render(<TrackCard track={baseTrack} onRequest={onRequest} requestState="idle" />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: /preferred quality/i }),
      "lossless",
    );
    await user.click(screen.getByRole("button", { name: /request/i }));
    expect(onRequest).toHaveBeenCalledWith(baseTrack, "lossless");
  });

  it("offers quality in terms the fulfillment tiers can actually promise", () => {
    // Not container names: "FLAC" was a promise the YouTube tier could never keep, which is
    // how a FLAC request came back as an Opus file.
    render(<TrackCard track={baseTrack} onRequest={vi.fn()} requestState="idle" />);
    const options = screen
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual(["any", "lossy", "lossless"]);
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
});
