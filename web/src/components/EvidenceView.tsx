import { useEffect, useRef, useState } from "react";
import type { CellSummary } from "../api";
import { AnswerView, ClaimList, CopyLink, Metadata, useCell, type Focus } from "./answer";
import { DocumentPages } from "./DocumentViewer";
import { StatusBadge } from "./ui";

/** Checking one answer against its document, full screen: the answer and its claims on the
 * left, the document's pages on the right with the focused claim's quotes highlighted.
 * ← → move to the previous or next document in this column (in the grid's current order),
 * ↑ ↓ move between claims, Esc closes. */
export function EvidenceView(props: {
  summary: CellSummary;
  initialFocus: Focus;
  position: { index: number; total: number } | null;
  onPrev: (() => void) | null;
  onNext: (() => void) | null;
  onClose: () => void;
}) {
  const { summary, initialFocus, position, onPrev, onNext, onClose } = props;
  const { cell, doc, error } = useCell(summary, true);
  const [focus, setFocus] = useState<Focus>(initialFocus);

  // The focused claim (the first one by default) and quote (the claim's first located one).
  const claimCount = cell?.claims?.length ?? 0;
  const focusClaim = claimCount ? Math.min(Math.max(focus.claim ?? 1, 1), claimCount) : null;
  const firstQuote = cell?.citations.find(
    (c) => c.char_start !== null && (focusClaim === null || c.claim_no === focusClaim),
  );
  const active = focus.citation ?? firstQuote?.id ?? null;

  // Keep the focused claim in view on the left as ↑ ↓ move through a long list.
  const side = useRef<HTMLElement>(null);
  useEffect(() => {
    side.current?.querySelector(".claim.focused")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusClaim, cell]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || (e.target as HTMLElement).closest("input, textarea, select")) return;
      const moveClaim = (step: number) =>
        focusClaim !== null && setFocus({ claim: Math.min(Math.max(focusClaim + step, 1), claimCount), citation: null });
      const actions: Record<string, (() => void) | null | false> = {
        Escape: onClose,
        ArrowLeft: onPrev,
        ArrowRight: onNext,
        ArrowUp: () => moveClaim(-1),
        ArrowDown: () => moveClaim(1),
      };
      const action = actions[e.key];
      if (action) {
        e.preventDefault();
        action();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, focusClaim, claimCount]);

  return (
    <div className="overlay evidence-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="evidence" role="dialog" aria-modal="true" aria-label="Evidence">
        <div className="evidence-head">
          <div className="evidence-title">
            <span className="doc-id">
              {summary.doc_id} v{summary.version}
            </span>
            {cell && <span className="muted">{cell.key.replace(/_/g, " ")}</span>}
            <StatusBadge status={summary.status} />
          </div>
          {position && (
            <span className="muted small tabular">
              {position.index + 1} of {position.total} in this column
            </span>
          )}
          <button className="btn small" onClick={onPrev ?? undefined} disabled={!onPrev} title="Previous document (←)">
            ← Prev
          </button>
          <button className="btn small" onClick={onNext ?? undefined} disabled={!onNext} title="Next document (→)">
            Next →
          </button>
          <CopyLink />
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="evidence-body">
          <section className="evidence-side" ref={side}>
            {error && <div className="error">{error}</div>}
            {cell && (
              <>
                <h2 className="question">{cell.question}</h2>
                <AnswerView cell={cell} />
                {(cell.citations.length > 0 || !!cell.claims?.length) && (
                  <ClaimList cell={cell} focus={{ claim: focusClaim, citation: active }} onFocus={setFocus} />
                )}
                {cell.reasoning && (
                  <div className="block">
                    <div className="label">Reasoning</div>
                    <p className="reasoning">{cell.reasoning}</p>
                  </div>
                )}
                <Metadata cell={cell} />
                <div className="evidence-keys">
                  <kbd>↑</kbd> <kbd>↓</kbd> claims · <kbd>←</kbd> <kbd>→</kbd> documents · <kbd>Esc</kbd> close
                </div>
              </>
            )}
          </section>
          {doc && cell ? (
            <DocumentPages
              doc={doc}
              citations={cell.citations}
              claims={cell.claims}
              focusClaim={focusClaim}
              active={active}
            />
          ) : (
            <div className="doc-pane muted">Loading document…</div>
          )}
        </div>
      </div>
    </div>
  );
}
