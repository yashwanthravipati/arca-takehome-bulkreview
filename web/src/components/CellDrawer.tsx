import { useEffect, useState } from "react";
import { api, type CellDetail, type CellSummary, type Citation, type DocumentText } from "../api";
import { DocumentViewer } from "./DocumentViewer";
import { StatusBadge, fmtInt } from "./ui";

export function CellDrawer({ summary, onClose }: { summary: CellSummary; onClose: () => void }) {
  const [cell, setCell] = useState<CellDetail | null>(null);
  const [doc, setDoc] = useState<DocumentText | null>(null);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

  // Fetch the full cell again whenever the live grid reports a change to it.
  const version = `${summary.status}|${summary.short}|${summary.page}`;
  useEffect(() => {
    api.cell(summary.id).then(setCell, (e: Error) => setError(e.message));
  }, [summary.id, version]);
  useEffect(() => {
    api.document(summary.doc_id, summary.version).then(setDoc, (e: Error) => setError(e.message));
  }, [summary.doc_id, summary.version]);

  async function rerun() {
    setRerunning(true);
    setError(null);
    setActiveCitation(null);
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
        <CopyLink />
        <button className="btn small" onClick={rerun} disabled={rerunning || busy}>
          {rerunning ? "Queuing…" : "Rerun cell"}
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div className="drawer-body">
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
                <Evidence cell={cell} active={activeCitation} onSelect={setActiveCitation} />
              )}
            </>
          )}
        </section>
        {doc && cell ? (
          <DocumentViewer doc={doc} citations={cell.citations} claims={cell.claims} active={activeCitation} />
        ) : (
          <div className="doc-viewer muted">Loading document…</div>
        )}
      </div>
    </aside>
  );
}

/** The URL already points at this cell (#/reviews/<id>/cells/<cellId>); copy it to share. */
function CopyLink() {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Copy this link:", window.location.href); // clipboard blocked
    }
  }
  return (
    <button className="btn small" onClick={copy}>
      {copied ? "Copied" : "Copy link"}
    </button>
  );
}

function AnswerView({ cell }: { cell: CellDetail }) {
  if (cell.status === "held")
    return (
      <p className="muted">
        Not run yet: this column was tried on a few documents first. Run the rest from the column header, or rerun just
        this cell.
      </p>
    );
  if (cell.status === "pending") return <p className="muted">Queued. A worker will pick this cell up shortly.</p>;
  if (cell.status === "running") return <p className="muted">A worker is answering this cell…</p>;
  if (cell.status === "failed") return <p className="failed-label">Failed after {cell.attempts} attempts.</p>;
  if (cell.status === "not_found" || cell.answer === null) return <p className="not-found">Not found in this document.</p>;

  const answer = cell.answer;
  let body;
  if (Array.isArray(answer)) {
    body = answer.length ? (
      <ul>{answer.map((item, i) => <li key={i}>{item}</li>)}</ul>
    ) : (
      <p>None — the document states there are none.</p>
    );
  } else if (typeof answer === "boolean") {
    body = <p>{answer ? "Yes" : "No"}</p>;
  } else if (cell.answer_type === "date") {
    body = <p>{new Date(`${answer}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "long" })}</p>;
  } else {
    body = <p>{answer}</p>;
  }
  return (
    <div className="block answer-block">
      <div className="label">Answer</div>
      {body}
    </div>
  );
}

function Metadata({ cell }: { cell: CellDetail }) {
  if (!cell.model) return null;
  const promptTokens = (cell.input_tokens ?? 0) + (cell.cache_read_tokens ?? 0) + (cell.cache_write_tokens ?? 0);
  return (
    <div className="meta">
      <span>{cell.model}</span>
      <span>{fmtInt(cell.latency_ms ?? 0)} ms</span>
      <span>
        {fmtInt(promptTokens)} in ({fmtInt(cell.cache_read_tokens ?? 0)} cached) · {fmtInt(cell.output_tokens ?? 0)} out
      </span>
      <span>
        attempt {cell.attempts}
        {cell.last_error && <span className="warn"> · earlier error: {cell.last_error}</span>}
      </span>
    </div>
  );
}

/** Each claim of the answer with the quotes that support it. Cells answered before per-claim
 * citations have no claims; their citations show as one flat list. */
function Evidence(props: { cell: CellDetail; active: number | null; onSelect: (id: number) => void }) {
  const { cell, active, onSelect } = props;
  const item = (c: Citation) => <CitationItem key={c.id} citation={c} active={active === c.id} onSelect={onSelect} />;
  if (!cell.claims) {
    return (
      <div className="block">
        <div className="label">Citations ({cell.citations.length})</div>
        <ol className="citations">{cell.citations.map(item)}</ol>
      </div>
    );
  }
  return (
    <div className="block">
      <div className="label">Claims ({cell.claims.length})</div>
      <ol className="claims">
        {cell.claims.map((claim, i) => {
          const quotes = cell.citations.filter((c) => c.claim_no === i + 1);
          const check = cell.checks?.[i] ?? null;
          return (
            <li key={i} className="claim">
              <div className="claim-text">
                <span className="claim-no">{i + 1}</span>
                <span>{claim}</span>
              </div>
              {check && (
                <div className={`claim-check ${check.ok ? "ok" : "warn"}`}>
                  {check.ok ? "Supported by its quotes" : `Not supported: ${check.reason}`}
                </div>
              )}
              {quotes.length > 0 ? (
                <ol className="citations">{quotes.map(item)}</ol>
              ) : (
                <p className="warn small">No supporting quote.</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function CitationItem(props: { citation: Citation; active: boolean; onSelect: (id: number) => void }) {
  const { citation: c, active, onSelect } = props;
  const located = c.char_start !== null;
  return (
    <li
      className={`citation ${c.verified ? "" : "unverified"} ${active ? "active" : ""} ${located ? "clickable" : ""}`}
      onClick={() => located && onSelect(c.id)}
    >
      <div className="cite-meta">
        <span className="page-tag">{c.page_no ? `p.${c.page_no}` : "no page"}</span>
        <span className={c.verified ? "ok" : "warn"}>{c.verified ? "Verified" : "Not found in document"}</span>
        {c.match_method && c.match_method !== "none" && <span className="muted">{c.match_method} match</span>}
      </div>
      <q>{c.quote}</q>
    </li>
  );
}
