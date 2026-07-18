import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/useAuth";
import { BrandMark } from "./BrandMark";
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
          {user?.role === "admin" && (
            <NavLink to="/admin" className="shell__nav-link">
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
              <span className="shell__email">{user.email}</span>
              <button type="button" className="button button--ghost" onClick={() => logout()}>
                Sign out
              </button>
            </>
          ) : null}
        </div>
      </header>
      <main className="shell__main">
        <Outlet />
      </main>
    </div>
  );
}
