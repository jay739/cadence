import type { TrackResult } from "../types/api";
import "./TrackCard.css";

const SOURCE_LABEL: Record<string, string> = {
  apple: "Apple",
  musicbrainz: "MusicBrainz",
  spotify: "Spotify",
  prowlarr: "Prowlarr",
};

function formatDuration(seconds: number | null): string | null {
  if (seconds == null) return null;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatSize(bytes: number | null): string | null {
  if (bytes == null) return null;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

interface TrackCardProps {
  track: TrackResult;
  onRequest: (track: TrackResult) => void;
  requestState: "idle" | "requesting" | "requested" | "error";
}

export function TrackCard({ track, onRequest, requestState }: TrackCardProps) {
  const duration = formatDuration(track.duration_seconds);
  const size = formatSize(track.size_bytes);

  return (
    <li className="track-card">
      <div className="track-card__art" aria-hidden="true">
        {track.artwork_url ? (
          <img src={track.artwork_url} alt="" loading="lazy" />
        ) : (
          <span className="track-card__art-fallback" />
        )}
      </div>
      <div className="track-card__body">
        <div className="track-card__title-row">
          <span className="track-card__title">{track.title}</span>
          <span className="track-card__source-badge">{SOURCE_LABEL[track.source]}</span>
        </div>
        <div className="track-card__artist">{track.artist}</div>
        <div className="track-card__meta mono">
          {track.album && <span>{track.album}</span>}
          {track.year && <span>{track.year}</span>}
          {duration && <span>{duration}</span>}
          {track.indexer && <span>{track.indexer}</span>}
          {size && <span>{size}</span>}
          {typeof track.seeders === "number" && <span>{track.seeders} seeders</span>}
        </div>
      </div>
      <button
        type="button"
        className="button button--primary track-card__action"
        disabled={requestState === "requesting" || requestState === "requested"}
        onClick={() => onRequest(track)}
      >
        {requestState === "requesting" && "Requesting…"}
        {requestState === "requested" && "Requested"}
        {requestState === "error" && "Try again"}
        {requestState === "idle" && "Request"}
      </button>
    </li>
  );
}
