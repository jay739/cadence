import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";
import type { RequestStatus } from "../types/api";

describe("StatusBadge", () => {
  it.each<[RequestStatus, string]>([
    ["pending", "Waiting for approval"],
    ["approved", "Approved, download queued"],
    ["downloading", "Downloading"],
    ["fulfilled", "Downloaded"],
    ["rejected", "Declined"],
    ["failed", "Download failed"],
  ])("renders the correct label for %s", (status, expectedLabel) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(expectedLabel)).toBeInTheDocument();
  });

  it("shows the groove spinner only for the downloading status", () => {
    const { rerender } = render(<StatusBadge status="downloading" />);
    expect(screen.getByRole("img", { name: /download in progress/i })).toBeInTheDocument();

    rerender(<StatusBadge status="fulfilled" />);
    expect(screen.queryByRole("img", { name: /download in progress/i })).not.toBeInTheDocument();
  });
});
