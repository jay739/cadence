import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceBadge } from "./SourceBadge";

/**
 * The badge names the source in the requester's language, never the operator's.
 *
 * "prowlarr", "slskd" and "ytdlp" are the names of Cadence's own components; a person who
 * asked for a song knows what a torrent, Soulseek and YouTube are. This mirrors how
 * StatusBadge phrases statuses, and it is the reason the backend sends the raw key and lets
 * the client do the wording.
 */
describe("SourceBadge", () => {
  it("calls the Prowlarr/qBittorrent tier a torrent", () => {
    render(<SourceBadge backend="prowlarr" />);
    expect(screen.getByText("from a torrent")).toBeInTheDocument();
    // The internal component name must not reach the requester.
    expect(screen.queryByText(/prowlarr/i)).not.toBeInTheDocument();
  });

  it("calls the slskd tier Soulseek", () => {
    render(<SourceBadge backend="slskd" />);
    expect(screen.getByText("from Soulseek")).toBeInTheDocument();
    expect(screen.queryByText(/slskd/i)).not.toBeInTheDocument();
  });

  it("calls the ytdlp tier YouTube", () => {
    render(<SourceBadge backend="ytdlp" />);
    expect(screen.getByText("from YouTube")).toBeInTheDocument();
    expect(screen.queryByText(/ytdlp/i)).not.toBeInTheDocument();
  });

  it("still says something useful for a tier it has no wording for yet", () => {
    // A tier added server-side reaches an older client as an unknown key. An honest raw label
    // beats rendering nothing for a track that definitely came from somewhere.
    render(<SourceBadge backend="futuretier" />);
    expect(screen.getByText("from futuretier")).toBeInTheDocument();
  });
});
