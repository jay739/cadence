// Mirrors backend/app/schemas/*.py. Kept as plain hand-written types rather than generated
// from the OpenAPI spec for Phase 1 to avoid adding a codegen step; revisit once the API is
// stable and native clients need the same contract (see MISSION.md Phase 2+).

export type UserRole = "user" | "admin";

export type AuthProvider = "local" | "navidrome";

// Which tier the rendered avatar came from. "navidrome" exists in the contract but is never
// produced today: Navidrome has no per-user avatar to fetch (see backend/app/services/avatars.py
// for the probe that established that), so accounts without an avatar_url get "generated".
export type AvatarSource = "url" | "navidrome" | "generated";

export interface User {
  id: string;
  email: string;
  role: UserRole;
  auth_provider: AuthProvider;
  // The raw stored value, null when unset. This is form state for the profile page; render
  // `avatar` instead.
  avatar_url: string | null;
  // The picture to actually render: always populated, either avatar_url or an inline SVG
  // doodle as a data: URI. Generated server-side so it needs no network and cannot fail.
  avatar: string;
  avatar_source: AvatarSource;
  // Only ever set for a "navidrome" account: where to send that user to manage their own
  // password, since Cadence never stores one for them.
  navidrome_url: string | null;
  created_at: string;
}

export interface RequestStatusCounts {
  pending: number;
  approved: number;
  downloading: number;
  fulfilled: number;
  rejected: number;
  failed: number;
  total: number;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export type SearchSourceName = "apple" | "musicbrainz" | "spotify" | "prowlarr";

// Requester's audio-quality preference: reorders download candidates, never a hard filter.
export type PreferredFormat = "any" | "mp3" | "flac";

export interface TrackResult {
  source: SearchSourceName;
  source_id: string;
  title: string;
  artist: string;
  album: string | null;
  duration_seconds: number | null;
  artwork_url: string | null;
  year: number | null;
  musicbrainz_recording_id: string | null;
  indexer: string | null;
  size_bytes: number | null;
  seeders: number | null;
  download_url: string | null;
  guid: string | null;
}

export interface SourceSearchResult {
  source: SearchSourceName;
  ok: boolean;
  error: string | null;
  results: TrackResult[];
}

export interface MultiSearchResponse {
  query: string;
  sources: SourceSearchResult[];
}

export type RequestStatus =
  | "pending"
  | "approved"
  | "downloading"
  | "fulfilled"
  | "rejected"
  | "failed";

export interface RequestRecord {
  id: string;
  user_id: string;
  title: string;
  artist: string;
  album: string | null;
  duration_seconds: number | null;
  artwork_url: string | null;
  source: SearchSourceName;
  source_id: string;
  musicbrainz_recording_id: string | null;
  preferred_format: PreferredFormat;
  status: RequestStatus;
  admin_note: string | null;
  failure_reason: string | null;
  final_file_path: string | null;
  fulfillment_attempts_count: number;
  created_at: string;
  updated_at: string;
}

export interface FulfillmentAttempt {
  id: string;
  backend: string;
  query_used: string;
  candidates_considered: unknown[] | null;
  chosen_candidate: Record<string, unknown> | null;
  outcome: string;
  detail: string | null;
  created_at: string;
}

export interface RequestDetail extends RequestRecord {
  attempts: FulfillmentAttempt[];
}

export interface IntegrationStatus {
  configured: boolean;
  reachable: boolean | null;
}

export interface HealthResponse {
  version: string;
  mode: "demo" | "live";
  database_ok: boolean;
  integrations: Record<string, IntegrationStatus>;
}

export interface ApiErrorBody {
  detail?: string;
  errors?: unknown;
}

// --- Admin-editable integration settings (mirrors backend/app/schemas/settings.py) ---

export type SettingsFieldSource = "env" | "db" | "unset";

export interface SettingsFieldValue {
  value: string | null;
  source: SettingsFieldSource;
  is_secret: boolean;
}

export interface ProwlarrSettings {
  prowlarr_url: SettingsFieldValue;
  prowlarr_api_key: SettingsFieldValue;
}

export interface QBittorrentSettings {
  qbittorrent_url: SettingsFieldValue;
  qbittorrent_username: SettingsFieldValue;
  qbittorrent_password: SettingsFieldValue;
}

export interface SlskdSettings {
  slskd_url: SettingsFieldValue;
  slskd_api_key: SettingsFieldValue;
}

export interface SpotifySettings {
  spotify_client_id: SettingsFieldValue;
  spotify_client_secret: SettingsFieldValue;
}

export interface LibrarySettings {
  music_library_path: SettingsFieldValue;
  library_path_pattern: SettingsFieldValue;
}

export type SettingsSourcePreference = "env" | "database";

export interface SourcePreference {
  preference: SettingsSourcePreference;
  forced_env: boolean;
}

export interface IntegrationSettings {
  prowlarr: ProwlarrSettings;
  qbittorrent: QBittorrentSettings;
  slskd: SlskdSettings;
  spotify: SpotifySettings;
  library: LibrarySettings;
  source_preferences: Record<SettingsIntegrationName, SourcePreference>;
}

export type SettingsIntegrationName = "prowlarr" | "qbittorrent" | "slskd" | "spotify" | "library";

export interface TestConnectionResult {
  ok: boolean;
  detail: string;
}

// --- Opt-in per-user Spotify personalization (mirrors
// backend/app/schemas/spotify_personalization.py) ---

export interface SpotifyConnectionStatus {
  connected: boolean;
  display_name: string | null;
}

export interface SpotifyPlaylistSummary {
  id: string;
  name: string;
  track_count: number;
}

export interface SpotifyPlaylistsResponse {
  playlists: SpotifyPlaylistSummary[];
}

// --- Already-in-library check (mirrors backend/app/schemas/library.py) ---

export interface LibraryCheckItem {
  // Whatever the caller uses to index its own results; echoed back untouched. The search UI
  // sends `${source}:${source_id}`.
  key: string;
  title: string;
  artist: string;
}

export interface LibraryMatch {
  key: string;
  navidrome_id: string;
  title: string;
  artist: string;
  album: string | null;
}

export interface LibraryCheckResponse {
  // False when no Navidrome service account is configured, in demo mode, or when every lookup
  // failed. Render nothing in that case: the answer is "unknown", not "not in your library".
  // A key missing from `matches` while `available` is true likewise only means "not confidently
  // found" — matching is deliberately conservative, so a miss is cheap and a false hit is not.
  available: boolean;
  matches: LibraryMatch[];
}
