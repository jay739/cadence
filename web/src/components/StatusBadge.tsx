import type { RequestStatus } from "../types/api";
import { Groove } from "./Groove";
import "./StatusBadge.css";

const LABELS: Record<RequestStatus, string> = {
  pending: "Pending review",
  approved: "Approved, queued",
  downloading: "Downloading",
  fulfilled: "Fulfilled",
  rejected: "Rejected",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: RequestStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      {status === "downloading" && <Groove size={14} />}
      {LABELS[status]}
    </span>
  );
}
