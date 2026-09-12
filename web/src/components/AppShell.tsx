import { NavLink } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { BrandMark } from "./BrandMark";
import { SetupBanner } from "./SetupBanner";
import { RouteTransition } from "./RouteTransition";
import { ThemeToggle } from "./ThemeToggle";
import "./AppShell.css";

export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">
          <BrandMark size={28} />
          <span className="shell__brand-text">Cadence</span>
        </div>
        <nav className="shell__nav" aria-label="Primary">
          <NavLink to="/search" className="shell__nav-link">
            Search
          </NavLink>
          <NavLink to="/requests" className="shell__nav-link">
            Your requests
          </NavLink>
          <NavLink to="/profile" className="shell__nav-link">
            Profile
          </NavLink>
          {/* `end`, or Approval queue stays active on /admin/settings too: NavLink marks a
              link active for any path starting with `to`, and Settings lives under /admin. */}
          {user?.role === "admin" && (
            <NavLink to="/admin" end className="shell__nav-link">
              Approval queue
            </NavLink>
          )}
          {user?.role === "admin" && (
            <NavLink to="/admin/settings" className="shell__nav-link">
              Settings
            </NavLink>
          )}
        </nav>
        <div className="shell__account">
          {user ? (
            <>
              {/* Decorative: the email beside it already identifies the account, so the image
                  carries no information a screen reader needs. `avatar` is always populated,
                  falling back to a generated doodle, so there is no empty state to handle. */}
              <img className="shell__avatar" src={user.avatar} alt="" />
              <span className="shell__email">{user.email}</span>
              <button type="button" className="button button--ghost" onClick={() => logout()}>
                Sign out
              </button>
            </>
          ) : null}
          <ThemeToggle />
        </div>
      </header>
      {user?.role === "admin" && <SetupBanner />}
      <main className="shell__main">
        <RouteTransition />
      </main>
    </div>
  );
}
