import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MyRequestsPage } from "./MyRequestsPage";
import { api } from "../api/client";
import type { RequestRecord } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      requests: { ...actual.api.requests, mine: vi.fn(), retry: vi.fn(), acceptLossy: vi.fn() },
    },
  };
});

const mockedApi = api as unknown as {
  requests: { mine: ReturnType<typeof vi.fn> };
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
    desired_quality: "any",
    lossy_accepted: false,
    status: "fulfilled",
    admin_note: null,
    failure_reason: null,
    final_file_path: "Arijit Singh/Tum Hi Ho.flac",
    fulfillment_attempts_count: 2,
    delivered_by: "slskd",
    download_progress_percent: null,
    download_phase: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

function renderList() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MyRequestsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MyRequestsPage: where a track came from", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("names the source of a delivered track, in the requester's language", async () => {
    mockedApi.requests.mine.mockResolvedValue([request()]);

    renderList();

    expect(await screen.findByText("from Soulseek")).toBeInTheDocument();
    // Never the internal tier key: this list is an ordinary user's request history.
    expect(screen.queryByText(/slskd/i)).not.toBeInTheDocument();
  });

  it("credits the tier that actually delivered, not the first one tried", async () => {
    // The backend resolves this from the audit trail (last DOWNLOAD_COMPLETED wins); the list
    // must render whatever it was told rather than assuming the usual Prowlarr-first path.
    mockedApi.requests.mine.mockResolvedValue([request({ delivered_by: "ytdlp" })]);

    renderList();

    expect(await screen.findByText("from YouTube")).toBeInTheDocument();
    expect(screen.queryByText("from a torrent")).not.toBeInTheDocument();
  });

  it("shows no source on a request that has not delivered anything", async () => {
    mockedApi.requests.mine.mockResolvedValue([
      request({ status: "pending", delivered_by: null, final_file_path: null }),
    ]);

    renderList();

    await screen.findByText("Tum Hi Ho");
    expect(screen.queryByTestId("source-badge")).not.toBeInTheDocument();
  });

  it("does not claim a source for a request that is still downloading", async () => {
    // delivered_by can already be set from a previous completed attempt on a retried request;
    // the badge still belongs only to a finished one.
    mockedApi.requests.mine.mockResolvedValue([
      request({ status: "downloading", delivered_by: "slskd" }),
    ]);

    renderList();

    await screen.findByText("Tum Hi Ho");
    expect(screen.queryByTestId("source-badge")).not.toBeInTheDocument();
  });
});

describe("MyRequestsPage: how far along a download is", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a real percentage for a torrent download", async () => {
    mockedApi.requests.mine.mockResolvedValue([
      request({
        status: "downloading",
        delivered_by: null,
        final_file_path: null,
        download_progress_percent: 63,
        download_phase: "Downloading from the torrent swarm",
      }),
    ]);

    renderList();

    const row = (await screen.findByText("Tum Hi Ho")).closest("li")!;
    expect(within(row).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "63");
    expect(within(row).getByText(/63%/)).toBeInTheDocument();
  });

  it("shows phase text and no bar where the tier cannot measure itself", async () => {
    // The YouTube tier reports a job state and nothing else, so there is no honest number to
    // draw. A bar here would be invented, which is worse than none at all.
    mockedApi.requests.mine.mockResolvedValue([
      request({
        status: "downloading",
        delivered_by: null,
        final_file_path: null,
        download_progress_percent: null,
        download_phase: "Fetching the audio from YouTube",
      }),
    ]);

    renderList();

    const row = (await screen.findByText("Tum Hi Ho")).closest("li")!;
    expect(within(row).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(row).getByText(/Fetching the audio from YouTube/)).toBeInTheDocument();
  });

  it("shows no progress at all on a request that is not downloading", async () => {
    mockedApi.requests.mine.mockResolvedValue([
      request({ download_progress_percent: 99, download_phase: "Almost there" }),
    ]);

    renderList();

    await screen.findByText("Tum Hi Ho");
    // A finished request must not keep rendering the progress of the transfer that ended.
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(/Almost there/)).not.toBeInTheDocument();
  });
});
