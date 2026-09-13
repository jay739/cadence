import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DownloadProgress } from "./DownloadProgress";

/**
 * The rule under test: a bar exists only where the server measured a real percentage.
 *
 * Cadence's three fulfillment tiers are not equal here -- qBittorrent and slskd both report a
 * genuine fraction, and the ytdlp-shim reports none at all. Rendering an indeterminate or
 * timer-driven bar for that third case would tell the requester a download is progressing
 * when nothing has said so, which is why the null case must render text and no progressbar.
 */
describe("DownloadProgress", () => {
  it("draws a real bar when the tier reported a percentage", () => {
    render(<DownloadProgress percent={63.4} phase="Downloading from the torrent swarm" />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "63");
    expect(screen.getByText(/63%/)).toBeInTheDocument();
    expect(screen.getByText(/Downloading from the torrent swarm/)).toBeInTheDocument();
  });

  it("shows the phase alone, with no bar, when no number exists", () => {
    render(<DownloadProgress percent={null} phase="Fetching the audio from YouTube" />);

    // The assertion that matters: no progressbar at all, rather than one sitting at zero or
    // animating on a timer.
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText(/Fetching the audio from YouTube/)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("renders nothing when the server has said nothing", () => {
    const { container } = render(<DownloadProgress percent={null} phase={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("reports a zero percentage as a real zero, not as unknown", () => {
    // 0 and null are different facts: a transfer that has moved no bytes yet genuinely is at
    // 0%, and the tier said so. Only null means "this source cannot tell us".
    render(<DownloadProgress percent={0} phase="Waiting for peers" />);

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("clamps a percentage the backend over-reports", () => {
    render(<DownloadProgress percent={140} phase="Downloading" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});
