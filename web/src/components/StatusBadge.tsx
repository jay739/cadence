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
};

export function StatusBadge({ status }: { status: RequestStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      {status === "downloading" && <Groove size={14} />}
      {LABELS[status]}
    </span>
  );
}
