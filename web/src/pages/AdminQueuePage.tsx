import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RequestRecord } from "../types/api";
import "./AdminQueuePage.css";

export function AdminQueuePage() {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});

  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-pending"],
    queryFn: () => api.requests.adminPending(),
  });

  const approve = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => api.requests.approve(id, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-pending"] });
    },
  });

  const reject = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => api.requests.reject(id, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-pending"] });
    },
  });

  function noteFor(request: RequestRecord): string {
    return notes[request.id] ?? "";
  }

  return (
    <div>
      <div className="page-header">
        <h1>Approval queue</h1>
        <p>
          Approve to enqueue fulfillment (Prowlarr first, Soulseek fallback), or reject with an
          optional note the requester will see.
        </p>
      </div>

      {isLoading && <p>Loading the queue…</p>}
      {isError && <p className="form-error">Couldn't load the queue.</p>}
      {data && data.length === 0 && (
        <div className="empty-state">Nothing waiting on approval right now.</div>
      )}

      {data && data.length > 0 && (
        <ul className="queue-list">
          {data.map((request) => (
            <li key={request.id} className="card queue-row">
              <div className="queue-row__info">
                <Link to={`/requests/${request.id}`} className="queue-row__title">
                  {request.title}
                </Link>
                <div className="queue-row__artist">{request.artist}</div>
                {request.album && <div className="queue-row__album mono">{request.album}</div>}
              </div>
              <input
                type="text"
                className="queue-row__note"
                placeholder="Optional note"
                value={noteFor(request)}
                onChange={(e) =>
                  setNotes((prev) => ({
                    ...prev,
                    [request.id]: e.target.value,
                  }))
                }
              />
              <div className="queue-row__actions">
                <button
                  type="button"
                  className="button button--primary"
                  disabled={approve.isPending}
                  onClick={() => approve.mutate({ id: request.id, note: noteFor(request) })}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="button button--danger"
                  disabled={reject.isPending}
                  onClick={() => reject.mutate({ id: request.id, note: noteFor(request) })}
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
