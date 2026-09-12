import { useState } from "react";
import type { DesiredQuality, LibraryMatch, TrackResult } from "../types/api";
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
  onRequest: (track: TrackResult, desiredQuality: DesiredQuality) => void;
  requestState: "idle" | "requesting" | "requested" | "error";
  // The Navidrome recording this track was confidently matched to, if any. Optional so the
  // call sites that have no library lookup available render exactly as before.
  libraryMatch?: LibraryMatch | null;
}

export function TrackCard({ track, onRequest, requestState, libraryMatch }: TrackCardProps) {
  const duration = formatDuration(track.duration_seconds);
  const size = formatSize(track.size_bytes);
  // Lossless is the one ask that is a guarantee rather than a preference: if nothing lossless
  // turns up anywhere, the request comes back and asks rather than quietly delivering a lossy
  // file. "Any" and "Lossy" only reorder candidates server-side.
  const [desiredQuality, setDesiredQuality] = useState<DesiredQuality>("any");

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
          {libraryMatch && (
            <span
              className="track-card__library-badge"
              // Naming what was matched is what makes the claim checkable: the whole risk of
              // this badge is talking someone out of a request they actually wanted.
              title={`Matched "${libraryMatch.title}" by ${libraryMatch.artist}${
                libraryMatch.album ? ` on ${libraryMatch.album}` : ""
              }`}
            >
              In your library
            </span>
          )}
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
      <div className="track-card__actions">
        <select
          className="track-card__format"
          aria-label="Preferred quality"
          value={desiredQuality}
          disabled={requestState === "requesting" || requestState === "requested"}
          onChange={(e) => setDesiredQuality(e.target.value as DesiredQuality)}
        >
          <option value="any">Any quality</option>
          <option value="lossy">Lossy (smaller)</option>
          <option value="lossless">Lossless only</option>
        </select>
        <button
          type="button"
          className="button button--primary track-card__action"
          disabled={requestState === "requesting" || requestState === "requested"}
          onClick={() => onRequest(track, desiredQuality)}
        >
          {requestState === "requesting" && "Requesting…"}
          {requestState === "requested" && "Requested"}
          {requestState === "error" && "Try again"}
          {requestState === "idle" && "Request"}
        </button>
      </div>
    </li>
  );
}
