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
        pickCandidate: vi.fn(),
      },
    },
  };
});

const mockedApi = api as unknown as {
  requests: {
    detail: ReturnType<typeof vi.fn>;
    retry: ReturnType<typeof vi.fn>;
    acceptLossy: ReturnType<typeof vi.fn>;
    pickCandidate: ReturnType<typeof vi.fn>;
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

function attemptWithCandidates(overrides: Record<string, unknown> = {}) {
  return {
    id: "att-1",
    backend: "ytdlp",
    query_used: "Sooraj Santhosh Labbar Bomma",
    candidates_considered: [
      {
        backend: "ytdlp",
        identifier: "wrongpick01",
        name: "Labbar Bomma Full Song Audio Jukebox",
        similarity: 0.685,
        quality: "lossy",
        extra: {},
      },
      {
        backend: "ytdlp",
        identifier: "rightpick02",
        name: "Sooraj Santhosh, Ranina Reddy - Labbar Bomma",
        similarity: 0.612,
        quality: "lossy",
        extra: {},
      },
    ],
    chosen_candidate: { name: "Labbar Bomma Full Song Audio Jukebox" },
    outcome: "download_failed",
    detail: "ytdlp-shim job ended in state 'failed'",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("RequestDetailPage: overriding a wrong automatic pick", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("offers each considered candidate as something to pick instead", async () => {
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({ status: "failed", attempts: [attemptWithCandidates()] }),
    );

    renderDetailPage();

    // The candidate list is behind a disclosure, so open it the way a reader would.
    await userEvent.setup().click(await screen.findByText(/2 candidates considered/));

    expect(screen.getByText("Sooraj Santhosh, Ranina Reddy - Labbar Bomma")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Use this one" })).toHaveLength(2);
  });

  it("sends the attempt and index of the candidate that was clicked", async () => {
    const user = userEvent.setup();
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({ status: "failed", attempts: [attemptWithCandidates()] }),
    );
    mockedApi.requests.pickCandidate.mockResolvedValue(requestDetail({ status: "approved" }));

    renderDetailPage();
    await user.click(await screen.findByText(/2 candidates considered/));
    // The SECOND candidate: the whole point is overriding the top-scoring pick.
    await user.click(screen.getAllByRole("button", { name: "Use this one" })[1]);

    await waitFor(() =>
      expect(mockedApi.requests.pickCandidate).toHaveBeenCalledWith("req-1", "att-1", 1),
    );
  });

  it("surfaces the backend's own refusal rather than a generic message", async () => {
    const user = userEvent.setup();
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({ status: "failed", attempts: [attemptWithCandidates()] }),
    );
    mockedApi.requests.pickCandidate.mockRejectedValue(
      new ApiError(
        409,
        null,
        "This request asked for a lossless copy, and that candidate is lossy.",
      ),
    );

    renderDetailPage();
    await user.click(await screen.findByText(/2 candidates considered/));
    await user.click(screen.getAllByRole("button", { name: "Use this one" })[0]);

    expect(await screen.findByText(/asked for a lossless copy/)).toBeVisible();
  });

  it("does not offer to re-pick a request whose file is already in the library", async () => {
    // Re-running fulfillment would overwrite it, so the server refuses; not offering the
    // action keeps the UI from dangling a button that can only ever 409.
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({
        status: "fulfilled",
        final_file_path: "Sooraj Santhosh/Labbar Bomma.opus",
        attempts: [attemptWithCandidates()],
      }),
    );

    renderDetailPage();
    await userEvent.setup().click(await screen.findByText(/2 candidates considered/));

    expect(screen.queryByRole("button", { name: "Use this one" })).not.toBeInTheDocument();
  });

  it("does not offer to pick a candidate too old to carry an identifier", async () => {
    // The backend cannot act on these (it says so explicitly), so the row renders as history
    // without an action rather than as a button that always fails.
    mockedApi.requests.detail.mockResolvedValue(
      requestDetail({
        status: "failed",
        attempts: [
          attemptWithCandidates({
            candidates_considered: [{ name: "Old Row Without An Identifier", similarity: 0.7 }],
          }),
        ],
      }),
    );

    renderDetailPage();
    await userEvent.setup().click(await screen.findByText(/1 candidate considered/));

    expect(screen.getByText("Old Row Without An Identifier")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Use this one" })).not.toBeInTheDocument();
  });
});

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
