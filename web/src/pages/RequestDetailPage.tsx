import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import "./RequestDetailPage.css";

export function RequestDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["request", id],
    queryFn: () => api.requests.detail(id!),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "approved" || status === "downloading" ? 3000 : false;
    },
  });

  const retry = useMutation({
    mutationFn: () => api.requests.retry(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["request", id] });
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
    },
  });

  if (isLoading) return <p>Loading request…</p>;
  if (isError || !data) return <p className="form-error">Couldn't load this request.</p>;

  return (
    <div>
      <p>
        <Link to="/requests" className="mono">
          &larr; Your requests
        </Link>
      </p>

      <div className="card request-detail">
        <div className="request-detail__header">
          <div>
            <h1>{data.title}</h1>
            <p>{data.artist}</p>
          </div>
          <div className="request-detail__status">
            <StatusBadge status={data.status} />
            {data.status === "failed" && (
              <button
                type="button"
                className="button button--primary"
                disabled={retry.isPending}
                onClick={() => retry.mutate()}
              >
                Retry download
              </button>
            )}
          </div>
        </div>
        {retry.isError && (
          <p className="form-error">
            {retry.error instanceof ApiError
              ? retry.error.message
              : "Couldn't retry this request. Try again."}
          </p>
        )}

        <dl className="request-detail__meta">
          {data.album && (
            <>
              <dt>Album</dt>
              <dd>{data.album}</dd>
            </>
          )}
          <dt>Source</dt>
          <dd className="mono">{data.source}</dd>
          <dt>Preferred quality</dt>
          <dd className="mono">
            {data.preferred_format === "any" ? "Any" : data.preferred_format.toUpperCase()}
          </dd>
          {data.final_file_path && (
            <>
              <dt>Filed at</dt>
              <dd className="mono">{data.final_file_path}</dd>
            </>
          )}
          {data.admin_note && (
            <>
              <dt>Admin note</dt>
              <dd>{data.admin_note}</dd>
            </>
          )}
          {data.failure_reason && (
            <>
              <dt>Why it failed</dt>
              <dd>{data.failure_reason}</dd>
            </>
          )}
        </dl>
      </div>

      <h2 className="request-detail__attempts-heading">Download attempts</h2>
      {data.attempts.length === 0 ? (
        <div className="empty-state">
          No download attempts yet. This fills in once the request is approved.
        </div>
      ) : (
        <ol className="attempt-list">
          {data.attempts.map((attempt) => (
            <li key={attempt.id} className="card attempt-card">
              <div className="attempt-card__header">
                <span className="mono attempt-card__backend">{attempt.backend}</span>
                <span className="attempt-card__outcome">
                  {attempt.outcome.replaceAll("_", " ")}
                </span>
                <time className="attempt-card__time mono" dateTime={attempt.created_at}>
                  {new Date(attempt.created_at).toLocaleString()}
                </time>
              </div>
              <p className="mono attempt-card__query">query: {attempt.query_used}</p>
              {attempt.detail && <p className="attempt-card__detail">{attempt.detail}</p>}
              {attempt.chosen_candidate && (
                <p className="attempt-card__chosen">
                  Picked: <span className="mono">{String(attempt.chosen_candidate.name)}</span>
                </p>
              )}
              {attempt.candidates_considered && attempt.candidates_considered.length > 0 && (
                <details className="attempt-card__candidates">
                  <summary>
                    {attempt.candidates_considered.length} candidate
                    {attempt.candidates_considered.length === 1 ? "" : "s"} considered
                  </summary>
                  <ul>
                    {attempt.candidates_considered.map((candidate, idx) => (
                      <li key={idx} className="mono">
                        {JSON.stringify(candidate)}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
