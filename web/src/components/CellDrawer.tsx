import { useState } from "react";
import { api, type CellSummary } from "../api";
import { AnswerView, ClaimList, CopyLink, Metadata, NO_FOCUS, useCell, type Focus } from "./answer";
import { StatusBadge } from "./ui";

/** A quick look at one cell beside the grid: the answer and its claims. Checking it against
 * the document happens in the evidence view: Open evidence, or click a claim or a quote. */
export function CellDrawer(props: { summary: CellSummary; onClose: () => void; onOpenEvidence: (focus: Focus) => void }) {
  const { summary, onClose, onOpenEvidence } = props;
  const { cell, error, setError } = useCell(summary, false);
  const [rerunning, setRerunning] = useState(false);

  async function rerun() {
    setRerunning(true);
    setError(null);
    try {
      await api.rerun(summary.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRerunning(false);
    }
  }

  const busy = summary.status === "pending" || summary.status === "running";
  return (
    <aside className="drawer" aria-label="Cell details">
      <div className="drawer-head">
        <span className="drawer-title">
          <span className="doc-id">
            {summary.doc_id} v{summary.version}
          </span>
          {cell && <span className="muted"> · {cell.key.replace(/_/g, " ")}</span>}
        </span>
        <button className="btn small primary" onClick={() => onOpenEvidence(NO_FOCUS)}>
          Open evidence
        </button>
        <CopyLink />
        <button className="btn small" onClick={rerun} disabled={rerunning || busy}>
          {rerunning ? "Queuing…" : "Rerun cell"}
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <section className="cell-info">
        {error && <div className="error">{error}</div>}
        {cell && (
          <>
            <div className="question-row">
              <h2 className="question">{cell.question}</h2>
              <StatusBadge status={summary.status} />
            </div>
            <AnswerView cell={cell} />
            {cell.reasoning && (
              <div className="block">
                <div className="label">Reasoning</div>
                <p className="reasoning">{cell.reasoning}</p>
              </div>
            )}
            <Metadata cell={cell} />
            {(cell.citations.length > 0 || !!cell.claims?.length) && (
              <ClaimList cell={cell} focus={null} onFocus={onOpenEvidence} />
            )}
          </>
        )}
      </section>
    </aside>
  );
}
