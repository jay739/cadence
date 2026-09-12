import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage } from "./ProfilePage";
import { api } from "../api/client";
import { useAuth } from "../context/useAuth";
import type { AuthContextValue } from "../context/authContextInstance";
import type { RequestStatusCounts, User } from "../types/api";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      users: {
        stats: vi.fn(),
        updateAvatar: vi.fn(),
        updateEmail: vi.fn(),
        changePassword: vi.fn(),
      },
    },
  };
});

vi.mock("../context/useAuth");

const mockedApi = api as unknown as {
  users: {
    stats: ReturnType<typeof vi.fn>;
    updateAvatar: ReturnType<typeof vi.fn>;
    updateEmail: ReturnType<typeof vi.fn>;
    changePassword: ReturnType<typeof vi.fn>;
  };
};
const mockedUseAuth = vi.mocked(useAuth);

function localUser(overrides: Partial<User> = {}): User {
  return {
    id: "user-1",
    email: "listener@example.com",
    role: "user",
    auth_provider: "local",
    avatar_url: null,
    // What the backend sends for an account with no avatar_url set: a generated doodle as an
    // inline data: URI (see backend/app/services/avatars.py), truncated here.
    avatar: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    avatar_source: "generated",
    navidrome_url: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function navidromeUser(overrides: Partial<User> = {}): User {
  return {
    ...localUser(),
    auth_provider: "navidrome",
    navidrome_url: "http://navidrome.example.internal:4533",
    ...overrides,
  };
}

function stats(overrides: Partial<RequestStatusCounts> = {}): RequestStatusCounts {
  return {
    pending: 2,
    approved: 1,
    downloading: 5,
    fulfilled: 3,
    rejected: 4,
    failed: 6,
    awaiting_quality: 7,
    total: 28,
    ...overrides,
  };
}

function mockAuth(user: User, extra: Partial<AuthContextValue> = {}) {
  mockedUseAuth.mockReturnValue({
    user,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    setUser: vi.fn(),
    applySession: vi.fn(),
    ...extra,
  });
}

function renderProfilePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProfilePage />
    </QueryClientProvider>,
  );
}

describe("ProfilePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the request stats once loaded", async () => {
    mockAuth(localUser());
    mockedApi.users.stats.mockResolvedValue(stats());

    renderProfilePage();

    const total = await screen.findByText("Total");
    expect(within(total.closest<HTMLElement>(".stat-tile")!).getByText("28")).toBeInTheDocument();

    const pending = screen.getByText("Pending").closest<HTMLElement>(".stat-tile")!;
    expect(within(pending).getByText("2")).toBeInTheDocument();

    const failed = screen.getByText("Failed").closest<HTMLElement>(".stat-tile")!;
    expect(within(failed).getByText("6")).toBeInTheDocument();

    // Requests parked on a lossless-or-lossy decision get their own tile: they are the only
    // count on this page that is a to-do item rather than a record of what happened.
    const awaiting = screen.getByText("Needs your call").closest<HTMLElement>(".stat-tile")!;
    expect(within(awaiting).getByText("7")).toBeInTheDocument();
  });

  it("shows an error message if stats fail to load", async () => {
    mockAuth(localUser());
    mockedApi.users.stats.mockRejectedValue(new Error("network down"));

    renderProfilePage();

    expect(await screen.findByText("Couldn't load your stats.")).toBeInTheDocument();
  });

  it("shows a password-change form for a local account, never the Navidrome redirect", async () => {
    mockAuth(localUser());
    mockedApi.users.stats.mockResolvedValue(stats());

    renderProfilePage();
    await screen.findByText("Total");

    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /manage your password in navidrome/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the Navidrome redirect link for a Navidrome account, never a password form", async () => {
    mockAuth(navidromeUser());
    mockedApi.users.stats.mockResolvedValue(stats());

    renderProfilePage();
    await screen.findByText("Total");

    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: /manage your password in navidrome/i });
    expect(link).toHaveAttribute("href", "http://navidrome.example.internal:4533");
  });

  it("falls back to guidance text when a Navidrome account has no known server URL", async () => {
    mockAuth(navidromeUser({ navidrome_url: null }));
    mockedApi.users.stats.mockResolvedValue(stats());

    renderProfilePage();
    await screen.findByText("Total");

    expect(
      screen.queryByRole("link", { name: /manage your password in navidrome/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/ask your server admin/i)).toBeInTheDocument();
  });

  it("saving a new email calls the API and applies the result to the auth context", async () => {
    const setUser = vi.fn();
    mockAuth(localUser(), { setUser });
    mockedApi.users.stats.mockResolvedValue(stats());
    mockedApi.users.updateEmail.mockResolvedValue(localUser({ email: "new@example.com" }));
    const user = userEvent.setup();

    renderProfilePage();
    await screen.findByText("Total");

    const emailInput = screen.getByLabelText("Email");
    await user.clear(emailInput);
    await user.type(emailInput, "new@example.com");
    await user.click(screen.getByRole("button", { name: "Save email" }));

    await waitFor(() => {
      expect(mockedApi.users.updateEmail).toHaveBeenCalledWith("new@example.com");
    });
    await waitFor(() => {
      expect(setUser).toHaveBeenCalledWith(localUser({ email: "new@example.com" }));
    });
    expect(await screen.findByText("Email updated.")).toBeInTheDocument();
  });

  it("changing the password calls the API and applies the fresh session", async () => {
    const applySession = vi.fn();
    mockAuth(localUser(), { applySession });
    mockedApi.users.stats.mockResolvedValue(stats());
    mockedApi.users.changePassword.mockResolvedValue({
      access_token: "new-token",
      token_type: "bearer",
      user: localUser(),
    });
    const user = userEvent.setup();

    renderProfilePage();
    await screen.findByText("Total");

    await user.type(screen.getByLabelText("Current password"), "old-strong-password");
    await user.type(screen.getByLabelText("New password"), "a-brand-new-password-2");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => {
      expect(mockedApi.users.changePassword).toHaveBeenCalledWith(
        "old-strong-password",
        "a-brand-new-password-2",
      );
    });
    expect(applySession).toHaveBeenCalledWith({
      access_token: "new-token",
      token_type: "bearer",
      user: localUser(),
    });
    expect(await screen.findByText("Password changed.")).toBeInTheDocument();
  });

  describe("avatar preview", () => {
    it("shows the generated doodle when no avatar URL is set", async () => {
      mockAuth(localUser());
      mockedApi.users.stats.mockResolvedValue(stats());

      renderProfilePage();
      await screen.findByText("Total");

      const preview = document.querySelector(".profile-avatar-preview")!.querySelector("img");
      expect(preview).toHaveAttribute("src", localUser().avatar);
    });

    it("previews the supplied URL instead once one is set", async () => {
      mockAuth(localUser({ avatar_url: "https://example.com/me.png", avatar_source: "url" }));
      mockedApi.users.stats.mockResolvedValue(stats());

      renderProfilePage();
      await screen.findByText("Total");

      const preview = document.querySelector(".profile-avatar-preview")!.querySelector("img");
      expect(preview).toHaveAttribute("src", "https://example.com/me.png");
    });
  });
});
