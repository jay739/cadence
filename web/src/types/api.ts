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
  awaiting_quality: number;
  total: number;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export type SearchSourceName = "apple" | "musicbrainz" | "spotify" | "prowlarr";

// What the requester asks for, in terms the fulfillment tiers can actually promise. Naming a
// container ("FLAC") could not be promised: the YouTube tier has no lossless source at all.
// "any" and "lossy" reorder download candidates; "lossless" is the one hard filter, and a
// request that finds nothing lossless ends up asking rather than quietly settling.
export type DesiredQuality = "any" | "lossy" | "lossless";

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
  | "failed"
  // Nothing lossless was found anywhere, and the request is waiting on the requester to say
  // whether a lossy copy will do. Neither a failure nor a silent downgrade.
  | "awaiting_quality";

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
  desired_quality: DesiredQuality;
  // True once the requester has answered an "awaiting_quality" question with "lossy is fine".
  // The original desired_quality is left alone, so a request fulfilled exactly as asked is
  // still distinguishable from one the requester knowingly settled for.
  lossy_accepted: boolean;
  status: RequestStatus;
  admin_note: string | null;
  failure_reason: string | null;
  final_file_path: string | null;
  fulfillment_attempts_count: number;
  // Which fulfillment tier actually delivered the file, or null while nothing has been
  // delivered yet. The raw backend key ("prowlarr" | "slskd" | "ytdlp"), deliberately a plain
  // string like TierInsight.backend: the server reports whatever tier delivered, and the
  // requester-facing wording lives in the client (see SourceBadge), the same way StatusBadge
  // owns the wording for statuses.
  delivered_by: string | null;
  // How far along an in-flight download is, 0..100, or null when no honest number exists.
  // null is NOT 0: only the torrent and Soulseek tiers can report a real fraction, and the
  // YouTube tier reports none at all, so null means "no number to show" and must never be
  // rendered as an empty or invented bar.
  download_progress_percent: number | null;
  // What is happening right now, in the requester's language ("Receiving from a Soulseek
  // peer"). The only progress the YouTube tier can honestly give.
  download_phase: string | null;
  created_at: string;
  updated_at: string;
}

// One entry in an attempt's `candidates_considered`. Every field is optional because this is
// a historical record: rows written before a field existed simply do not carry it, and the
// UI has to render those without breaking. `identifier` in particular is what the backend
// needs to re-run a pick, so a row lacking it cannot be hand-picked (the backend says so, in
// those words, rather than the client guessing).
export interface FulfillmentCandidate {
  backend?: string;
  identifier?: string;
  name?: string;
  similarity?: number;
  quality?: string;
  extra?: Record<string, unknown> | null;
}

export interface FulfillmentAttempt {
  id: string;
  backend: string;
  query_used: string;
  candidates_considered: FulfillmentCandidate[] | null;
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
  // Only the capability bits the pre-login and general-user UI need. Which indexer/download
  // client exists, and whether any of them answer, is admin-only (see IntegrationsResponse).
  navidrome_login_available: boolean;
  spotify_search_available: boolean;
}

export interface IntegrationsResponse {
  mode: "demo" | "live";
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

// --- Admin-only fulfillment insights (mirrors backend/app/schemas/insights.py) ---

export interface TierInsight {
  // "prowlarr" | "slskd" | "ytdlp" today, but deliberately a plain string: the backend reports
  // any tier it finds rows for, so a tier added server-side shows up without a client release.
  backend: string;
  // Audit rows this tier wrote. Always >= requests_tried, since one successful pass writes
  // several (e.g. "grabbed" and then "download_completed").
  attempts: number;
  requests_tried: number;
  requests_delivered: number;
  // null when the tier never ran in the window. That is not the same as failing everything, so
  // it must never render as 0%: an unconfigured indexer did not lose, it was never asked.
  success_rate: number | null;
  outcomes: Record<string, number>;
}

export interface FailureInsight {
  outcome: string;
  count: number;
  backends: Record<string, number>;
}

export interface DailyVolumeInsight {
  // YYYY-MM-DD. Quiet days are present with requests: 0, never omitted, so the series is always
  // exactly window_days long and a gap reads as a gap.
  date: string;
  requests: number;
}

export interface TopArtistInsight {
  artist: string;
  requests: number;
}

export interface AdminInsights {
  window_days: number;
  // First day included in the window (YYYY-MM-DD, UTC).
  since: string;
  total_requests: number;
  status_counts: Record<RequestStatus, number>;
  tiers: TierInsight[];
  failures: FailureInsight[];
  daily_volume: DailyVolumeInsight[];
  top_artists: TopArtistInsight[];
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
  // found", because matching is deliberately conservative: a miss is cheap, a false hit is not.
  available: boolean;
  matches: LibraryMatch[];
}
