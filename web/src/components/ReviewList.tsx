import { useEffect, useState } from "react";
import { api, type ReviewListItem } from "../api";
import { NewReviewModal } from "./NewReviewModal";
import { Progress } from "./ui";

const REFRESH_MS = 3000;

export function ReviewList() {
  const [reviews, setReviews] = useState<ReviewListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.reviews().then(
        (r) => alive && (setReviews(r), setError(null)),
        (e: Error) => alive && setError(e.message),
      );
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  async function remove(r: ReviewListItem) {
    const unfinished = r.total - r.finished;
    const warning = unfinished ? ` ${unfinished} of its cells are still queued or running and will be dropped.` : "";
    const question = `Delete "${r.name}" with its ${r.total} answers and their citations?${warning} This can't be undone.`;
    if (!window.confirm(question)) return;
    try {
      await api.deleteReview(r.id);
      setReviews((list) => list?.filter((x) => x.id !== r.id) ?? null);
    } catch (e) {
      window.alert(`Could not delete "${r.name}": ${(e as Error).message}`);
    }
  }

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Reviews</h1>
          <p className="muted">Each review runs a set of questions across documents. Every answer cites its source.</p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}>
          New review
        </button>
      </div>

      {error && <div className="error">Could not load reviews: {error}</div>}
      <table className="list-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Template</th>
            <th>Created</th>
            <th className="progress-col">Progress</th>
            <th className="num">Needs review</th>
            <th className="num">Failed</th>
            <th className="row-actions" aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {reviews?.map((r) => (
            <tr key={r.id} onClick={() => (window.location.hash = `#/reviews/${r.id}`)}>
              <td>
                <a href={`#/reviews/${r.id}`} className="strong">
                  {r.name}
                </a>
              </td>
              <td className="muted">{r.template.replace(/_/g, " ")}</td>
              <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
              <td>
                <div className="progress-cell">
                  <Progress value={r.finished} total={r.total} />
                  <span className="muted tabular">
                    {r.finished}/{r.total} finished
                  </span>
                </div>
              </td>
              <td className="num">
                <span className={r.counts.needs_review ? "count-review" : "muted"}>{r.counts.needs_review}</span>
              </td>
              <td className="num">
                <span className={r.counts.failed ? "count-failed" : "muted"}>{r.counts.failed}</span>
              </td>
              <td className="row-actions">
                <button
                  className="delete-btn"
                  onClick={(e) => {
                    e.stopPropagation(); // the row itself opens the review
                    remove(r);
                  }}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {reviews?.length === 0 && <div className="empty">No reviews yet. Create one to get started.</div>}
      {creating && <NewReviewModal onClose={() => setCreating(false)} />}
    </main>
  );
}
