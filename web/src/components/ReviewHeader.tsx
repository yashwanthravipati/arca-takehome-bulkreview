import { api, type ReviewGrid, type Status } from "../api";
import { Progress, STATUS_LABEL, fmtCount } from "./ui";

const COUNTED: Status[] = ["done", "not_found", "needs_review", "failed", "running", "pending", "held"];

export function ReviewHeader(props: { grid: ReviewGrid; live: boolean; onAddColumn: () => void }) {
  const { grid, live, onAddColumn } = props;
  const { review, totals: t } = grid;
  const promptTokens = t.input_tokens + t.cache_read_tokens + t.cache_write_tokens;

  return (
    <header className="review-head">
      <div className="title-row">
        <div>
          <div className="crumbs">
            <a href="#/">Reviews</a> /
          </div>
          <h1>{review.name}</h1>
          <div className="muted small">
            {review.template.replace(/_/g, " ")} · {grid.rows.length} documents × {grid.columns.length} questions ·{" "}
            <span className={`live ${live ? "on" : ""}`}>{live ? "live" : "reconnecting…"}</span>
          </div>
        </div>
        <div className="actions">
          <button className="btn" onClick={onAddColumn}>
            Add column
          </button>
          <a className="btn" href={api.exportUrl(review.id)} download>
            Export CSV
          </a>
        </div>
      </div>

      <div className="stats">
        <div className="stat progress-stat">
          <Progress value={t.finished} total={t.total} />
          <span className="tabular">
            {t.finished}/{t.total} finished
          </span>
        </div>
        <div className="stat status-counts">
          {COUNTED.filter((s) => s !== "held" || t.counts.held > 0).map((s) => (
            <span key={s} className={`count count-${s}`}>
              <i className={`dot dot-${s}`} />
              {STATUS_LABEL[s]} <b className="tabular">{t.counts[s] ?? 0}</b>
            </span>
          ))}
        </div>
        <div className="stat">
          <span className="stat-label">Tokens</span>
          <span className="tabular">
            {fmtCount(promptTokens)} in · {fmtCount(t.output_tokens)} out
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Cache hit</span>
          <span className="tabular">{t.cache_hit_rate == null ? "–" : `${Math.round(t.cache_hit_rate * 100)}%`}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Est. cost</span>
          <span className="tabular">${t.cost_usd.toFixed(4)}</span>
        </div>
      </div>
    </header>
  );
}
