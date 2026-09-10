import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useLocation, Link } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { api, ApiError } from "../api/client";
import "./AuthPages.css";

type LoginMode = "local" | "navidrome";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<LoginMode>("local");
  // Undetermined until /api/health answers, so the Navidrome tab does not flash in and then
  // disappear once we learn the operator never set NAVIDROME_URL.
  const [navidromeAvailable, setNavidromeAvailable] = useState<boolean | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/search";

  useEffect(() => {
    let cancelled = false;
    api.health
      .get()
      .then((health) => {
        if (!cancelled) {
          setNavidromeAvailable(health.integrations.navidrome?.configured ?? false);
        }
      })
      .catch(() => {
        // /api/health is unauthenticated and should never be down for a real reason on a
        // working deployment; if it fails, default to hiding the tab rather than offering a
        // sign-in mode we cannot confirm exists.
        if (!cancelled) {
          setNavidromeAvailable(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(identifier, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next: LoginMode) {
    setMode(next);
    setIdentifier("");
    setError(null);
  }

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <h1>Sign in</h1>
        <p>Search for a track, request it, and track where it lands.</p>

        {navidromeAvailable && (
          <div className="auth-mode-toggle" role="tablist" aria-label="Sign-in method">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "local"}
              className={
                mode === "local" ? "auth-mode-toggle__tab is-active" : "auth-mode-toggle__tab"
              }
              onClick={() => switchMode("local")}
            >
              Email
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "navidrome"}
              className={
                mode === "navidrome" ? "auth-mode-toggle__tab is-active" : "auth-mode-toggle__tab"
              }
              onClick={() => switchMode("navidrome")}
            >
              Sign in with Navidrome
            </button>
          </div>
        )}

        {error && <p className="form-error">{error}</p>}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="identifier">
              {mode === "navidrome" ? "Navidrome username" : "Email"}
            </label>
            <input
              id="identifier"
              // Navidrome usernames are not email addresses, so type="email" would reject
              // valid input (browser-level validation, separate from anything the server
              // checks) the moment this tab is selected.
              type={mode === "navidrome" ? "text" : "email"}
              autoComplete={mode === "navidrome" ? "username" : "email"}
              required
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" className="button button--primary" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {mode === "local" && (
          <p className="form-note">
            New here? <Link to="/register">Create an account</Link>
          </p>
        )}
        {mode === "navidrome" && (
          <p className="form-note">
            Use the same username and password you sign into Navidrome with.
          </p>
        )}
      </div>
    </div>
  );
}
