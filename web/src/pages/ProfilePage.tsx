import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { useAuth } from "../context/useAuth";
import type { RequestStatus } from "../types/api";
import "./ProfilePage.css";

const STATUS_ORDER: RequestStatus[] = [
  "pending",
  "approved",
  "downloading",
  "fulfilled",
  "rejected",
  "failed",
];

const STATUS_LABELS: Record<RequestStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  downloading: "Downloading",
  fulfilled: "Fulfilled",
  rejected: "Rejected",
  failed: "Failed",
};

function StatsSection() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["profile-stats"],
    queryFn: () => api.users.stats(),
  });

  return (
    <section className="card profile-section">
      <div className="profile-section__header">
        <h2>Your requests</h2>
        <p>How many tracks you've asked for, by where they stand.</p>
      </div>

      {isLoading && <p>Loading your stats…</p>}
      {isError && <p className="form-error">Couldn't load your stats.</p>}

      {data && (
        <div className="profile-stats">
          <div className="stat-tile stat-tile--total">
            <span className="stat-tile__value">{data.total}</span>
            <span className="stat-tile__label">Total</span>
          </div>
          {STATUS_ORDER.map((status) => (
            <div className={`stat-tile stat-tile--${status}`} key={status}>
              <span className="stat-tile__value">{data[status]}</span>
              <span className="stat-tile__label">{STATUS_LABELS[status]}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function AvatarSection() {
  const { user, setUser } = useAuth();
  const [draft, setDraft] = useState(user?.avatar_url ?? "");
  const [error, setError] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  const save = useMutation({
    mutationFn: (avatarUrl: string) => api.users.updateAvatar(avatarUrl || null),
    onSuccess: (updated) => {
      setUser(updated);
      setError(null);
      setPreviewFailed(false);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not save your avatar URL.");
    },
  });

  const dirty = draft !== (user?.avatar_url ?? "");

  return (
    <section className="card profile-section">
      <div className="profile-section__header">
        <h2>Avatar</h2>
        <p>A link to an image, shown as your profile picture. No upload, just a URL.</p>
      </div>

      <div className="profile-avatar-row">
        <div className="profile-avatar-preview">
          {draft && !previewFailed ? (
            <img src={draft} alt="" onError={() => setPreviewFailed(true)} />
          ) : (
            <span className="profile-avatar-preview__fallback" aria-hidden="true" />
          )}
        </div>
        <div className="field profile-avatar-field">
          <label htmlFor="avatar_url">Avatar URL</label>
          <input
            id="avatar_url"
            type="url"
            placeholder="https://example.com/me.jpg"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setPreviewFailed(false);
            }}
          />
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}

      <div className="profile-section__actions">
        <button
          type="button"
          className="button button--primary"
          disabled={save.isPending || !dirty}
          onClick={() => save.mutate(draft)}
        >
          {save.isPending ? "Saving…" : "Save avatar"}
        </button>
      </div>
    </section>
  );
}

function EmailSection() {
  const { user, setUser } = useAuth();
  const [draft, setDraft] = useState(user?.email ?? "");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const save = useMutation({
    mutationFn: (email: string) => api.users.updateEmail(email),
    onSuccess: (updated) => {
      setUser(updated);
      setError(null);
      setSuccess(true);
    },
    onError: (err) => {
      setSuccess(false);
      setError(err instanceof ApiError ? err.message : "Could not update your email.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSuccess(false);
    save.mutate(draft);
  }

  const dirty = draft !== (user?.email ?? "");

  return (
    <section className="card profile-section">
      <div className="profile-section__header">
        <h2>Email</h2>
        <p>Used to sign in and, eventually, for notifications.</p>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setSuccess(false);
            }}
          />
        </div>

        {error && <p className="form-error">{error}</p>}
        {success && <p className="profile-success">Email updated.</p>}

        <div className="profile-section__actions">
          <button
            type="submit"
            className="button button--primary"
            disabled={save.isPending || !dirty}
          >
            {save.isPending ? "Saving…" : "Save email"}
          </button>
        </div>
      </form>
    </section>
  );
}

function LocalPasswordForm() {
  const { applySession } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const save = useMutation({
    mutationFn: (payload: { currentPassword: string; newPassword: string }) =>
      api.users.changePassword(payload.currentPassword, payload.newPassword),
    onSuccess: (result) => {
      applySession(result);
      setError(null);
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
    },
    onError: (err) => {
      setSuccess(false);
      setError(err instanceof ApiError ? err.message : "Could not change your password.");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSuccess(false);
    if (newPassword.length < 10) {
      setError("New password needs at least 10 characters.");
      return;
    }
    save.mutate({ currentPassword, newPassword });
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="current_password">Current password</label>
        <input
          id="current_password"
          type="password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(e) => {
            setCurrentPassword(e.target.value);
            setSuccess(false);
          }}
        />
      </div>
      <div className="field">
        <label htmlFor="new_password">New password</label>
        <input
          id="new_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          value={newPassword}
          onChange={(e) => {
            setNewPassword(e.target.value);
            setSuccess(false);
          }}
        />
        <span className="form-note">At least 10 characters.</span>
      </div>

      {error && <p className="form-error">{error}</p>}
      {success && <p className="profile-success">Password changed.</p>}

      <div className="profile-section__actions">
        <button type="submit" className="button button--primary" disabled={save.isPending}>
          {save.isPending ? "Changing…" : "Change password"}
        </button>
      </div>
    </form>
  );
}

function NavidromeRedirect({ navidromeUrl }: { navidromeUrl: string | null }) {
  return (
    <div className="profile-navidrome-redirect">
      <p>
        This account signs in through Navidrome, so Cadence never stores a password for it. Manage
        your password in Navidrome instead.
      </p>
      {navidromeUrl ? (
        <a href={navidromeUrl} target="_blank" rel="noreferrer" className="button button--ghost">
          Manage your password in Navidrome
        </a>
      ) : (
        <p className="form-note">Ask your server admin for the address of your Navidrome server.</p>
      )}
    </div>
  );
}

function PasswordSection() {
  const { user } = useAuth();
  const isNavidrome = user?.auth_provider === "navidrome";

  return (
    <section className="card profile-section">
      <div className="profile-section__header">
        <h2>Password</h2>
      </div>
      {isNavidrome && user ? (
        <NavidromeRedirect navidromeUrl={user.navidrome_url} />
      ) : (
        <LocalPasswordForm />
      )}
    </section>
  );
}

export function ProfilePage() {
  return (
    <div>
      <div className="page-header">
        <h1>Profile</h1>
        <p>Your stats, and how you sign in.</p>
      </div>

      <div className="profile-grid">
        <StatsSection />
        <AvatarSection />
        <EmailSection />
        <PasswordSection />
      </div>
    </div>
  );
}
