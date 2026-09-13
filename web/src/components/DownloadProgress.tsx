import "./DownloadProgress.css";

/**
 * What an in-flight download is doing right now.
 *
 * The central rule: a bar is drawn ONLY when the server sent a real number. Two of Cadence's
 * three fulfillment tiers can measure themselves (qBittorrent reports a 0..1 fraction, slskd
 * reports percentComplete on each transfer), and the third cannot -- the ytdlp-shim runs
 * yt-dlp to completion and exposes nothing but a job state.
 *
 * So when `percent` is null this renders phase text alone. It deliberately does NOT fall back
 * to an indeterminate/animated bar or one that advances on a timer: a bar that moves while
 * nothing is known claims progress the server never reported, which is worse than silence
 * because it makes a stalled transfer look healthy. Phase text ("Waiting for a Soulseek peer")
 * tells the requester what is actually happening and stays true when nothing is moving.
 */
export function DownloadProgress({
  percent,
  phase,
}: {
  percent: number | null;
  phase: string | null;
}) {
  // Nothing known at all: say nothing rather than render an empty shell. The status badge
  // already says "Downloading", so a blank progress row would add noise, not information.
  if (percent === null && !phase) return null;

  const rounded = percent === null ? null : Math.round(Math.max(0, Math.min(100, percent)));

  return (
    <div className="download-progress">
      {rounded !== null && (
        <div
          className="download-progress__track"
          role="progressbar"
          aria-valuenow={rounded}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={phase ?? "Download progress"}
        >
          <div className="download-progress__fill" style={{ width: `${rounded}%` }} />
        </div>
      )}
      <span className="download-progress__caption">
        {phase}
        {rounded !== null && <span className="download-progress__percent"> {rounded}%</span>}
      </span>
    </div>
  );
}
