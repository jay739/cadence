import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "./HomePage";
import { api } from "../api/client";
import type { RequestRecord, RequestStatusCounts } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      requests: { ...actual.api.requests, mine: vi.fn() },
      users: { ...actual.api.users, stats: vi.fn() },
    },
  };
});

const mockedApi = api as unknown as {
  requests: { mine: ReturnType<typeof vi.fn> };
  users: { stats: ReturnType<typeof vi.fn> };
};

function request(overrides: Partial<RequestRecord> = {}): RequestRecord {
  return {
    id: "req-1",
    user_id: "user-1",
    title: "Tum Hi Ho",
    artist: "Arijit Singh",
    album: "Aashiqui 2",
    duration_seconds: 262,
    artwork_url: null,
    source: "apple",
    source_id: "track-1",
    musicbrainz_recording_id: null,
    status: "pending",
    admin_note: null,
    failure_reason: null,
    final_file_path: null,
    fulfillment_attempts_count: 0,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  } as RequestRecord;
}

function counts(overrides: Partial<RequestStatusCounts> = {}): RequestStatusCounts {
  return {
    pending: 0,
    approved: 0,
    downloading: 0,
    fulfilled: 0,
    rejected: 0,
    failed: 0,
    total: 0,
    ...overrides,
  } as RequestStatusCounts;
}

function renderHome() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HomePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.users.stats.mockResolvedValue(counts());
  });

  it("invites a brand-new account to search instead of showing empty panels", async () => {
    mockedApi.requests.mine.mockResolvedValue([]);
    renderHome();
    expect(await screen.findByText("Nothing requested yet")).toBeInTheDocument();
    // The empty state must not also render the two panels, or it reads as broken.
    expect(screen.queryByText("Still in flight")).not.toBeInTheDocument();
  });

  it("separates what is still in flight from what has landed", async () => {
    mockedApi.requests.mine.mockResolvedValue([
      request({ id: "a", title: "Kesariya", status: "downloading" }),
      request({ id: "b", title: "Butta Bomma", status: "fulfilled" }),
    ]);
    renderHome();

    const inFlight = (await screen.findByRole("heading", { name: "Still in flight" })).closest(
      "section",
    )!;
    const landed = screen.getByRole("heading", { name: "Recently landed" }).closest("section")!;

    expect(within(inFlight).getByText("Kesariya")).toBeInTheDocument();
    expect(within(landed).getByText("Butta Bomma")).toBeInTheDocument();
    // A landed track must not also be listed as in flight.
    expect(within(inFlight).queryByText("Butta Bomma")).not.toBeInTheDocument();
  });

  it("puts the request that needs an answer above ones merely waiting", async () => {
    mockedApi.requests.mine.mockResolvedValue([
      request({ id: "waiting", title: "Just Pending", status: "pending" }),
      request({ id: "needs-you", title: "Needs An Answer", status: "awaiting_quality" }),
    ]);
    renderHome();

    const rows = await screen.findAllByRole("listitem");
    // awaiting_quality is actionable by the reader; pending is not, so it sorts first.
    expect(rows[0]).toHaveTextContent("Needs An Answer");
  });

  it("surfaces a load failure with a way out rather than an empty page", async () => {
    mockedApi.requests.mine.mockRejectedValue(new Error("boom"));
    renderHome();
    expect(await screen.findByText(/Couldn't load your requests/)).toBeInTheDocument();
  });
});
