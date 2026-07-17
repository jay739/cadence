import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import "./ListPages.css";

export function MyRequestsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["my-requests"],
    queryFn: () => api.requests.mine(),
    refetchInterval: (query) => {
      const rows = query.state.data ?? [];
      const hasActive = rows.some((r) => r.status === "approved" || r.status === "downloading");
      return hasActive ? 4000 : false;
    },
  });

  return (
    <div>
      <div className="page-header">
        <h1>Your requests</h1>
        <p>Every track you've asked for, and where it stands.</p>
      </div>

      {isLoading && <p>Loading your requests…</p>}
      {isError && <p className="form-error">Couldn't load your requests. Try reloading.</p>}
      {data && data.length === 0 && (
        <div className="empty-state">
          Nothing requested yet. <Link to="/search">Find a track</Link> to get started.
        </div>
      )}
      {data && data.length > 0 && (
        <ul className="request-list">
          {data.map((request) => (
            <li key={request.id} className="card request-row">
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
                <StatusBadge status={request.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
