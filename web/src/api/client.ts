import type {
  ApiErrorBody,
  AuthResponse,
  HealthResponse,
  IntegrationSettings,
  MultiSearchResponse,
  RequestDetail,
  PreferredFormat,
  RequestRecord,
  RequestStatusCounts,
  SearchSourceName,
  SettingsIntegrationName,
  SpotifyConnectionStatus,
  SpotifyPlaylistsResponse,
  TestConnectionResult,
  TrackResult,
  User,
} from "../types/api";

// Same-origin by default: the web client is served by the same container as the API and
// never talks to any third-party source directly (Spotify/Prowlarr/etc. keys stay server-side,
// per the mission's security requirements). VITE_API_BASE_URL exists only for local dev
// against a backend running on a different port.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody | null;

  constructor(status: number, body: ApiErrorBody | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
  if (token) {
    localStorage.setItem("cadence_token", token);
  } else {
    localStorage.removeItem("cadence_token");
  }
}

export function loadStoredToken(): string | null {
  authToken = localStorage.getItem("cadence_token");
  return authToken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (authToken) {
    headers.set("Authorization", `Bearer ${authToken}`);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json") ? await response.json() : null;

  if (!response.ok) {
    const message =
      (body as ApiErrorBody | null)?.detail ?? `Request failed with status ${response.status}`;
    throw new ApiError(response.status, body, message);
  }

  return body as T;
}

export const api = {
  auth: {
    register: (email: string, password: string) =>
      request<AuthResponse>("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    // identifier is a local account's email OR a Navidrome username; the backend accepts
    // either under the same field (see backend/app/schemas/user.py's LoginIn).
    login: (identifier: string, password: string) =>
      request<AuthResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ identifier, password }),
      }),
    logout: () => request<void>("/api/auth/logout", { method: "POST" }),
    me: () => request<User>("/api/auth/me"),
  },
  search: {
    query: (q: string, limit = 20) =>
      request<MultiSearchResponse>(
        `/api/search?${new URLSearchParams({ q, limit: String(limit) })}`,
      ),
    // EventSource (used by useSearchStream) makes its own GET directly, bypassing the request()
    // helper above, since EventSource cannot set an Authorization header; the browser attaches
    // the session cookie automatically instead, same-origin.
    streamUrl: (q: string, limit = 20) =>
      `${API_BASE}/api/search/stream?${new URLSearchParams({ q, limit: String(limit) })}`,
    // Resolves one exact track from a pasted Spotify/Apple Music link, as an alternative to
    // free-text search. A 422 (unrecognized link) or 404 (recognized platform, track not
    // found) surfaces via ApiError with the backend's specific, actionable message already in
    // `.message` — see backend/app/services/link_resolve.py for the exact wording.
    resolveLink: (url: string) =>
      request<TrackResult>("/api/search/resolve-link", {
        method: "POST",
        body: JSON.stringify({ url }),
      }),
  },
  requests: {
    create: (payload: {
      source: SearchSourceName;
      source_id: string;
      title: string;
      artist: string;
      album: string | null;
      duration_seconds: number | null;
      artwork_url: string | null;
      musicbrainz_recording_id: string | null;
      preferred_format: PreferredFormat;
    }) =>
      request<RequestRecord>("/api/requests", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    mine: () => request<RequestRecord[]>("/api/requests/mine"),
    detail: (id: string) => request<RequestDetail>(`/api/requests/${id}`),
    adminPending: () => request<RequestRecord[]>("/api/requests/admin/pending"),
    adminAll: () => request<RequestRecord[]>("/api/requests/admin/all"),
    approve: (id: string, note?: string) =>
      request<RequestRecord>(`/api/requests/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ note: note ?? null }),
      }),
    reject: (id: string, note?: string) =>
      request<RequestRecord>(`/api/requests/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ note: note ?? null }),
      }),
    retry: (id: string) =>
      request<RequestRecord>(`/api/requests/${id}/retry`, {
        method: "POST",
      }),
  },
  health: {
    get: () => request<HealthResponse>("/api/health"),
  },
  users: {
    stats: () => request<RequestStatusCounts>("/api/users/me/stats"),
    updateAvatar: (avatarUrl: string | null) =>
      request<User>("/api/users/me/avatar", {
        method: "PATCH",
        body: JSON.stringify({ avatar_url: avatarUrl }),
      }),
    updateEmail: (email: string) =>
      request<User>("/api/users/me/email", {
        method: "PATCH",
        body: JSON.stringify({ email }),
      }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<AuthResponse>("/api/users/me/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
  },
  adminSettings: {
    get: () => request<IntegrationSettings>("/api/admin/settings"),
    update: (integration: SettingsIntegrationName, fields: Record<string, string>) =>
      request<IntegrationSettings>(`/api/admin/settings/${integration}`, {
        method: "PUT",
        body: JSON.stringify(fields),
      }),
    testConnection: (integration: SettingsIntegrationName, fields: Record<string, string>) =>
      request<TestConnectionResult>(`/api/admin/settings/${integration}/test`, {
        method: "POST",
        body: JSON.stringify(fields),
      }),
  },
  spotifyPersonalization: {
    status: () => request<SpotifyConnectionStatus>("/api/spotify/status"),
    // A real top-level navigation, not a fetch: Spotify's own authorize page can't be reached
    // via XHR/fetch (it's a full-page redirect chain ending back at our /callback), so this
    // just hands the browser the URL to go to rather than returning a parsed response.
    connectUrl: () => `${API_BASE}/api/spotify/connect`,
    disconnect: () => request<void>("/api/spotify/disconnect", { method: "POST" }),
    savedTracks: () => request<TrackResult[]>("/api/spotify/library/saved-tracks"),
    playlists: () => request<SpotifyPlaylistsResponse>("/api/spotify/library/playlists"),
    playlistTracks: (playlistId: string) =>
      request<TrackResult[]>(`/api/spotify/library/playlists/${playlistId}/tracks`),
  },
};
