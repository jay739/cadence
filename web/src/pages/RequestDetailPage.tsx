import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { DownloadProgress } from "../components/DownloadProgress";
import { SourceBadge } from "../components/SourceBadge";
import { StatusBadge } from "../components/StatusBadge";
import type { DesiredQuality } from "../types/api";
import "./RequestDetailPage.css";

const QUALITY_LABELS: Record<DesiredQuality, string> = {
  any: "Any",
  lossy: "Lossy",
  lossless: "Lossless",
};

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

  const acceptLossy = useMutation({
    mutationFn: () => api.requests.acceptLossy(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["request", id] });
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
      // The stats tiles count this status too, so a stale "needs your call" count would
      // outlive the decision that just cleared it.
      queryClient.invalidateQueries({ queryKey: ["profile-stats"] });
    },
  });

  // Which candidate the in-flight pick is for, so only the row that was actually clicked
  // shows a pending label rather than every button on the page going busy at once.
  const [pickTarget, setPickTarget] = useState<{ attemptId: string; index: number } | null>(null);

  const pick = useMutation({
    mutationFn: ({ attemptId, index }: { attemptId: string; index: number }) => {
      setPickTarget({ attemptId, index });
      return api.requests.pickCandidate(id!, attemptId, index);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["request", id] });
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
      // A pick can resolve an awaiting_quality request, which the profile tiles count.
      queryClient.invalidateQueries({ queryKey: ["profile-stats"] });
    },
    onSettled: () => setPickTarget(null),
  });

  if (isLoading) return <p>Loading request…</p>;
  if (isError || !data) return <p className="form-error">Couldn't load this request.</p>;

  // Mirrors the backend's PICKABLE_STATUSES (see app/services/manual_candidate.py). A
  // fulfilled request is excluded because re-running fulfillment would overwrite the file
  // already in the library, and a downloading one because a transfer is already in flight;
  // the server refuses both regardless, this just avoids offering an action that cannot work.
  const canPick =
    data.status === "failed" ||
    data.status === "awaiting_quality" ||
    data.status === "pending" ||
    data.status === "approved";

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
            {data.status === "fulfilled" && data.delivered_by && (
              <SourceBadge backend={data.delivered_by} />
            )}
            {data.status === "downloading" && (
              <DownloadProgress
                percent={data.download_progress_percent}
                phase={data.download_phase}
              />
            )}
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
        {pick.isError && (
          <p className="form-error">
            {pick.error instanceof ApiError
              ? pick.error.message
              : "Couldn't switch to that candidate. Try again."}
          </p>
        )}

        {data.status === "awaiting_quality" && (
          <section className="quality-prompt" aria-labelledby="quality-prompt-heading">
            <h2 id="quality-prompt-heading" className="quality-prompt__heading">
              Nothing lossless found
            </h2>
            <p className="quality-prompt__body">
              You asked for a lossless copy of this track, and every source Cadence searched only
              had it as lossy audio. YouTube, the last fallback, never has a lossless source at all.
              Nothing has been downloaded, so this is your call: accept a lossy copy, or leave the
              request here and try again later, when a lossless release may have turned up.
            </p>
            {acceptLossy.isError && (
              <p className="form-error">
                {acceptLossy.error instanceof ApiError
                  ? acceptLossy.error.message
                  : "Couldn't accept a lossy copy. Try again."}
              </p>
            )}
            <button
              type="button"
              className="button button--primary"
              disabled={acceptLossy.isPending}
              onClick={() => acceptLossy.mutate()}
            >
              {acceptLossy.isPending ? "Accepting…" : "Accept a lossy copy"}
            </button>
          </section>
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
          <dt>Requested quality</dt>
          <dd className="mono">
            {QUALITY_LABELS[data.desired_quality]}
            {data.lossy_accepted && " (lossy copy accepted)"}
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
                  {canPick && (
                    <p className="attempt-card__pick-hint">
                      Cadence picked the top-scoring result. If it chose wrong, pick the right one
                      and it will download that instead of scoring the search again.
                    </p>
                  )}
                  <ul>
                    {attempt.candidates_considered.map((candidate, idx) => (
                      <li key={idx} className="candidate-row">
                        <span className="candidate-row__text">
                          <span className="mono candidate-row__name">
                            {candidate.name ?? JSON.stringify(candidate)}
                          </span>
                          <span className="candidate-row__meta">
                            {[
                              typeof candidate.similarity === "number"
                                ? `similarity ${candidate.similarity.toFixed(3)}`
                                : null,
                              candidate.quality && candidate.quality !== "unknown"
                                ? candidate.quality
                                : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        </span>
                        {canPick && candidate.identifier && (
                          <button
                            type="button"
                            className="button button--ghost candidate-row__pick"
                            disabled={pick.isPending}
                            onClick={() => pick.mutate({ attemptId: attempt.id, index: idx })}
                          >
                            {pick.isPending && pickTarget?.attemptId === attempt.id
                              ? "Starting…"
                              : "Use this one"}
                          </button>
                        )}
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
