import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StatStrip } from "../components/StatStrip";
import { StatusBadge } from "../components/StatusBadge";
import type { RequestRecord, RequestStatus } from "../types/api";
import { landedWhen } from "./homeTime";
import "./ListPages.css";
import "./HomePage.css";

/**
 * The landing page after sign-in: what is happening with the tracks you asked for.
 *
 * Every number and row here comes from endpoints that already existed (`/api/requests/mine`
 * and `/api/users/me/stats`) -- the data was being collected and simply never surfaced
 * anywhere except a list you had to go looking for.
 *
 * Both queries deliberately reuse the exact query keys the requests list and the profile page
 * use ("my-requests" / "profile-stats"), so navigating between them is a cache hit rather than
 * a second round trip, and a mutation that invalidates one updates this page too.
 */

// Anything not yet fulfilled or rejected. Ordered by how much it wants the reader's attention,
// not by status enum order: a request parked on a question they have to answer, or one that
// failed and can be retried, is actionable, while a pending one is just waiting.
const IN_FLIGHT_PRIORITY: RequestStatus[] = [
  "awaiting_quality",
  "failed",
  "downloading",
  "approved",
  "pending",
];

const IN_FLIGHT_LIMIT = 5;
const LANDED_LIMIT = 5;

function isInFlight(request: RequestRecord): boolean {
  return IN_FLIGHT_PRIORITY.includes(request.status);
}

function byAttention(a: RequestRecord, b: RequestRecord): number {
  const rank = IN_FLIGHT_PRIORITY.indexOf(a.status) - IN_FLIGHT_PRIORITY.indexOf(b.status);
  if (rank !== 0) return rank;
  return b.updated_at.localeCompare(a.updated_at);
}

function RequestRow({ request, trailing }: { request: RequestRecord; trailing: React.ReactNode }) {
  return (
    <li className="request-row home-row">
      <Link to={`/requests/${request.id}`} className="request-row__link">
        <div className="request-row__art">
          {request.artwork_url ? (
            <img src={request.artwork_url} alt="" loading="lazy" />
          ) : (
            <span className="request-row__art-fallback" />
          )}
        </div>
        <div className="request-row__body">
          <div className="request-row__title">{request.title}</div>
          <div className="request-row__artist">{request.artist}</div>
        </div>
        {trailing}
      </Link>
    </li>
  );
}

export function HomePage() {
  const {
    data: requests,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["my-requests"],
    queryFn: () => api.requests.mine(),
    // Same rule as the requests list: poll only while something is actually moving, so an
    // idle dashboard left open in a tab is not a background request generator.
    refetchInterval: (query) => {
      const rows = query.state.data ?? [];
      const hasActive = rows.some((r) => r.status === "approved" || r.status === "downloading");
      return hasActive ? 4000 : false;
    },
  });

  const { data: stats } = useQuery({
    queryKey: ["profile-stats"],
    queryFn: () => api.users.stats(),
  });

  const inFlight = (requests ?? []).filter(isInFlight).sort(byAttention);
  const landed = (requests ?? [])
    .filter((r) => r.status === "fulfilled")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const nothingYet = requests !== undefined && requests.length === 0;

  return (
    <div>
      <div className="page-header home-header">
        <div>
          <h1>What's happening</h1>
          <p>Where the tracks you've asked for stand, and what's landed lately.</p>
        </div>
        <Link to="/search" className="button button--primary home-header__cta">
          Find a track
        </Link>
      </div>

      {isLoading && <p>Loading your music…</p>}
      {isError && (
        <p className="form-error">
          Couldn't load your requests. Reload the page, or open{" "}
          <Link to="/requests">your requests</Link> directly.
        </p>
      )}

      {nothingYet && (
        <div className="empty-state home-empty">
          <h2>Nothing requested yet</h2>
          <p>
            Search for a single track by name, or paste a Spotify or Apple Music link. Once you ask
            for something, this page follows it from approval through to the file landing in your
            library.
          </p>
          <Link to="/search" className="button button--primary home-empty__cta">
            Find your first track
          </Link>
        </div>
      )}

      {requests !== undefined && requests.length > 0 && (
        <>
          {stats && (
            <StatStrip
              stats={[
                {
                  label: "In flight",
                  value: stats.pending + stats.approved + stats.downloading,
                },
                // Tinted only when it is non-zero: this is the one tile that is a to-do item
                // rather than a fact, so it should look inert on the (usual) days when nothing
                // is waiting on the reader.
                {
                  label: "Needs your call",
                  value: stats.awaiting_quality,
                  tone: stats.awaiting_quality > 0 ? "warn" : "neutral",
                },
                { label: "Landed", value: stats.fulfilled, tone: "good" },
                { label: "Asked for", value: stats.total },
              ]}
            />
          )}

          <div className="home-grid">
            <section className="card home-panel">
              <div className="home-panel__header">
                <h2>Still in flight</h2>
                <Link to="/requests" className="home-panel__more">
                  See all {requests.length}
                </Link>
              </div>
              {inFlight.length === 0 ? (
                <p className="home-panel__empty">
                  Nothing in flight. Everything you've asked for has been settled one way or
                  another.
                </p>
              ) : (
                <ul className="home-list">
                  {inFlight.slice(0, IN_FLIGHT_LIMIT).map((request) => (
                    <RequestRow
                      key={request.id}
                      request={request}
                      trailing={<StatusBadge status={request.status} />}
                    />
                  ))}
                </ul>
              )}
              {inFlight.length > IN_FLIGHT_LIMIT && (
                <p className="home-panel__footnote">
                  and {inFlight.length - IN_FLIGHT_LIMIT} more in flight
                </p>
              )}
            </section>

            <section className="card home-panel">
              <div className="home-panel__header">
                <h2>Recently landed</h2>
              </div>
              {landed.length === 0 ? (
                <p className="home-panel__empty">
                  Nothing has landed yet. Tracks show up here once the file is in your library.
                </p>
              ) : (
                <ul className="home-list">
                  {landed.slice(0, LANDED_LIMIT).map((request) => (
                    <RequestRow
                      key={request.id}
                      request={request}
                      trailing={
                        <span className="home-landed__when mono">
                          {landedWhen(request.updated_at)}
                        </span>
                      }
                    />
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
