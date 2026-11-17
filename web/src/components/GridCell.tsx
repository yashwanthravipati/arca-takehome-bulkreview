import type { AnswerType, CellSummary, Question } from "../api";
import { listItems, today } from "../rows";

export function GridCell(props: {
  cell: CellSummary | undefined;
  question: Question;
  active: boolean;
  dimmed: boolean;
  match: boolean; // its answer matches the search
  onOpen: (cellId: number) => void;
}) {
  const { cell, question, active, dimmed, match, onOpen } = props;
  if (!cell) return <div className="cell cell-empty" />;

  const classes = ["cell", `cell-${cell.status}`, active && "active", dimmed && "dimmed", match && "match"];
  return (
    <button
      className={classes.filter(Boolean).join(" ")}
      onClick={() => onOpen(cell.id)}
      title={cell.short ?? undefined}
      data-doc={cell.doc_id}
      data-key={question.key}
      data-cell-id={cell.id}
    >
      <CellContent cell={cell} type={question.answer_type} />
    </button>
  );
}

function CellContent({ cell, type }: { cell: CellSummary; type: AnswerType }) {
  switch (cell.status) {
    case "pending":
      return <span className="shimmer" aria-label="Pending" />;
    case "running":
      return (
        <span className="running">
          <span className="spinner" /> Running
        </span>
      );
    case "held":
      return <span className="not-run">Not run yet</span>;
    case "not_found":
      return <span className="not-found">Not found</span>;
    case "failed":
      return <span className="failed-label">Failed</span>;
    case "needs_review":
      return (
        <>
          <Answer cell={cell} type={type} />
          <span className="review-tag">Review</span>
        </>
      );
    case "done":
      return (
        <>
          <Answer cell={cell} type={type} />
          {cell.page !== null && <span className="page-tag">p.{cell.page}</span>}
        </>
      );
  }
}

/** The one-line answer, shown according to the column's answer type. */
function Answer({ cell, type }: { cell: CellSummary; type: AnswerType }) {
  const short = cell.short;
  if (short === null) return <span className="answer muted">–</span>;
  if (type === "boolean") return <span className="answer"><span className="pill">{short === "yes" ? "Yes" : "No"}</span></span>;
  if (type === "date") return <span className="answer"><DateValue iso={short} /></span>;
  if (type === "list") {
    const items = listItems({ ...cell, short });
    if (items.length === 0) return <span className="answer muted">None</span>;
    // Inline lines (not blocks) so the three-line clamp still applies.
    return (
      <span className="answer">
        {items.map((item, i) => (
          <span key={i}>
            {i > 0 && <br />}
            {items.length > 1 && <span className="bullet">•</span>}
            {item}
          </span>
        ))}
      </span>
    );
  }
  return <span className="answer">{short}</span>;
}

/** "Mar 1, 2025" plus how far it is from today, e.g. "8 mo ago" (useful for due dates). */
function DateValue({ iso }: { iso: string }) {
  const at = Date.parse(`${iso}T00:00:00`);
  if (Number.isNaN(at)) return <>{iso}</>;
  const days = Math.round((at - Date.parse(`${today()}T00:00:00`)) / 86_400_000);
  const abs = Math.abs(days);
  const span = abs < 45 ? `${abs} d` : abs < 548 ? `${Math.round(abs / 30.4)} mo` : `${(abs / 365).toFixed(1)} y`;
  const relative = days === 0 ? "today" : days > 0 ? `in ${span}` : `${span} ago`;
  return (
    <>
      {new Date(at).toLocaleDateString(undefined, { dateStyle: "medium" })}{" "}
      <span className={`rel ${days < 0 ? "past" : ""}`}>{relative}</span>
    </>
  );
}
