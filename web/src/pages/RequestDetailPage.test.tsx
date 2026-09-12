import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequestDetailPage } from "./RequestDetailPage";
import { api } from "../api/client";
import type { RequestDetail } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      requests: {
        ...actual.api.requests,
        detail: vi.fn(),
        retry: vi.fn(),
        acceptLossy: vi.fn(),
      },
    },
  };
});

const mockedApi = api as unknown as {
  requests: {
    detail: ReturnType<typeof vi.fn>;
    retry: ReturnType<typeof vi.fn>;
    acceptLossy: ReturnType<typeof vi.fn>;
  };
};

function requestDetail(overrides: Partial<RequestDetail> = {}): RequestDetail {
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
    desired_quality: "lossless",
    lossy_accepted: false,
    status: "awaiting_quality",
    admin_note: null,
    failure_reason: null,
    final_file_path: null,
    fulfillment_attempts_count: 3,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    attempts: [],
    ...overrides,
  };
}

function renderDetailPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/requests/req-1"]}>
        <Routes>
          <Route path="/requests/:id" element={<RequestDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RequestDetailPage: the lossless-or-lossy question", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("explains what happened and offers the lossy copy for an awaiting request", async () => {
    mockedApi.requests.detail.mockResolvedValue(requestDetail());

    renderDetailPage();

    expect(await screen.findByRole("heading", { name: "Nothing lossless found" })).toBeVisible();
    // The reason has to name the actual constraint, not just "we couldn't get it": a requester
    // deciding whether to settle needs to know waiting for YouTube to improve is not an option.
    expect(screen.getByText(/never has a lossless source/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has been downloaded/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept a lossy copy" })).toBeEnabled();
  });

  it("sends the acceptance and refreshes the request", async () => {
    const user = userEvent.setup();
    mockedApi.requests.detail.mockResolvedValue(requestDetail());
    mockedApi.requests.acceptLossy.mockResolvedValue(
      requestDetail({ status: "approved", lossy_accepted: true }),
    );

    renderDetailPage();
    await user.click(await screen.findByRole("button", { name: "Accept a lossy copy" }));

    await waitFor(() => expect(mockedApi.requests.acceptLossy).toHaveBeenCalledWith("req-1"));
  });

  it("surfaces the backend's own message when accepting fails", async () => {
    const user = userEvent.setup();
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    mockedApi.requests.detail.mockResolvedValue(requestDetail());
    mockedApi.requests.acceptLossy.mockRejectedValue(
      new ApiError(
        409,
        null,
        "Only a request waiting on a quality decision can accept a lossy copy",
      ),
    );

    renderDetailPage();
    await user.click(await screen.findByRole("button", { name: "Accept a lossy copy" }));

    expect(await screen.findByText(/Only a request waiting on a quality decision/)).toBeVisible();
  });

  it("does not ask anything on a request that is not waiting on a decision", async () => {
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({ status: "fulfilled", final_file_path: "Arijit Singh/Tum Hi Ho.flac" }),
    );

    renderDetailPage();

    expect(await screen.findByText("Filed at")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept a lossy copy" })).not.toBeInTheDocument();
  });

  it("records that a fulfilled request was settled for, not delivered as asked", async () => {
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({
        status: "fulfilled",
        lossy_accepted: true,
        final_file_path: "Arijit Singh/Tum Hi Ho.opus",
      }),
    );

    renderDetailPage();

    expect(await screen.findByText(/Lossless \(lossy copy accepted\)/)).toBeInTheDocument();
  });
});
