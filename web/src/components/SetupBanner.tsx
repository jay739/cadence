import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import "./SetupBanner.css";

const DISMISS_KEY = "cadence_setup_banner_dismissed";

/**
 * Shown to an admin, on every page, until at least one fulfillment integration is configured.
 * A fresh self-hosted deployment has nothing wired up (Prowlarr/qBittorrent/slskd/Spotify are
 * all optional at boot so the app can start and serve search regardless), which otherwise gives
 * no signal that approved requests will never actually download anything until this is done.
 * Dismissing hides it for the rest of this browser session only: it reappears next session
 * (and, more importantly, for any other admin) until the operator actually configures something.
 */
export function SetupBanner() {
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem(DISMISS_KEY) === "true");

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health.get(),
    staleTime: 60_000,
  });

  if (dismissed || !healthQuery.data || healthQuery.data.mode === "demo") {
    return null;
  }

  const nothingConfigured = Object.values(healthQuery.data.integrations).every(
    (status) => !status.configured,
  );
  if (!nothingConfigured) {
    return null;
  }

  function dismiss() {
    sessionStorage.setItem(DISMISS_KEY, "true");
    setDismissed(true);
  }

  return (
    <div className="setup-banner" role="status">
      <span>
        No download sources are set up yet: people can search for songs, but nothing can be
        downloaded until you add your indexer (Prowlarr), download client (qBittorrent), or Soulseek
        (slskd) details.
      </span>
      <div className="setup-banner__actions">
        <Link to="/admin/settings" className="button button--primary">
          Go to Settings
        </Link>
        <button type="button" className="button button--ghost" onClick={dismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
