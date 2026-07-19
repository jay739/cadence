import "./Groove.css";

/**
 * The signature element: a single concentric groove on a record label, with a tonearm cue
 * dot resting on it. Used wherever the app is actively spinning on one track (the
 * "downloading" status badge, the fulfillment-in-progress indicator). Deliberately a single
 * groove, not a whole record's worth of grooves, echoing Cadence's one-track premise.
 */
export function Groove({ size = 20 }: { size?: number }) {
  return (
    <span
      className="groove"
      style={{ width: size, height: size }}
      role="img"
      aria-label="Download in progress"
    >
      <span className="groove__ring" />
      <span className="groove__label" />
      <span className="groove__cue" />
    </span>
  );
}
