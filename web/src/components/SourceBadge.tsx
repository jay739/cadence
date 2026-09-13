import "./SourceBadge.css";

/**
 * Where a delivered track actually came from.
 *
 * Phrased for the person who asked for the song, not for the person who runs the server.
 * "prowlarr" is the indexer Cadence searched, but what the requester received is a torrent;
 * "ytdlp" is the tool, but the source is YouTube. Naming the internal component here would
 * leak the operator's stack into an ordinary user's request history, which this app avoids
 * everywhere else (see StatusBadge's wording for the same reasoning).
 */
const LABELS: Record<string, string> = {
  prowlarr: "from a torrent",
  slskd: "from Soulseek",
  ytdlp: "from YouTube",
};

export function SourceBadge({ backend }: { backend: string }) {
  // A tier added server-side reaches this client before the client knows a friendly name for
  // it. Showing the raw key is a poor label but an honest one, and it beats rendering nothing
  // at all for a track that definitely came from somewhere.
  const label = LABELS[backend] ?? `from ${backend}`;
  return (
    <span className={`source-badge source-badge--${backend}`} data-testid="source-badge">
      {label}
    </span>
  );
}
