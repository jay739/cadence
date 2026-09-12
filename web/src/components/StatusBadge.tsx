import type { RequestStatus } from "../types/api";
import { Groove } from "./Groove";
import "./StatusBadge.css";

const LABELS: Record<RequestStatus, string> = {
  pending: "Waiting for approval",
  approved: "Approved, download queued",
  downloading: "Downloading",
  fulfilled: "Downloaded",
  rejected: "Declined",
  failed: "Download failed",
  // Phrased as the question it is. "Awaiting quality" would read like a system state the
  // requester can only wait out, when in fact nothing moves until they answer.
  awaiting_quality: "Nothing lossless found, your call",
};

export function StatusBadge({ status }: { status: RequestStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      {status === "downloading" && <Groove size={14} />}
      {LABELS[status]}
    </span>
  );
}
