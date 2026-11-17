// The parts of one answer, shared by the cell drawer (a quick look) and the evidence view
// (checking the answer against its document): the answer, its claims with their quotes, the
// call's metadata, and a link to share.
import { useEffect, useState } from "react";
import { api, type CellDetail, type CellSummary, type Citation, type DocumentText } from "../api";
import { fmtInt } from "./ui";

/** Which claim, and which of its quotes, is in focus; null means the first one. */
export interface Focus {
  claim: number | null;
  citation: number | null;
}
export const NO_FOCUS: Focus = { claim: null, citation: null };

/** The full cell, fetched again whenever the live grid reports a change to it, and (if asked
 * for) its document. */
export function useCell(summary: CellSummary, withDocument: boolean) {
  const [cell, setCell] = useState<CellDetail | null>(null);
  const [doc, setDoc] = useState<DocumentText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = `${summary.status}|${summary.short}|${summary.page}`;
  useEffect(() => {
    api.cell(summary.id).then(setCell, (e: Error) => setError(e.message));
  }, [summary.id, version]);
  useEffect(() => {
    if (withDocument) api.document(summary.doc_id, summary.version).then(setDoc, (e: Error) => setError(e.message));
  }, [withDocument, summary.doc_id, summary.version]);
  return { cell, doc, error, setError };
}

export function AnswerView({ cell }: { cell: CellDetail }) {
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

export function Metadata({ cell }: { cell: CellDetail }) {
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

/** Each claim of the answer with its check and the quotes that support it. Clicking a claim or
 * a quote focuses it. Cells answered before per-claim citations have no claims; their
 * citations show as one flat list. */
export function ClaimList(props: { cell: CellDetail; focus: Focus | null; onFocus: (focus: Focus) => void }) {
  const { cell, focus, onFocus } = props;
  const item = (c: Citation, claimNo: number | null) => (
    <CitationItem
      key={c.id}
      citation={c}
      active={focus?.citation === c.id}
      onSelect={() => onFocus({ claim: claimNo, citation: c.id })}
    />
  );
  if (!cell.claims) {
    return (
      <div className="block">
        <div className="label">Citations ({cell.citations.length})</div>
        <ol className="citations">{cell.citations.map((c) => item(c, null))}</ol>
      </div>
    );
  }
  return (
    <div className="block">
      <div className="label">Claims ({cell.claims.length})</div>
      <ol className="claims">
        {cell.claims.map((claim, i) => {
          const n = i + 1;
          const quotes = cell.citations.filter((c) => c.claim_no === n);
          const check = cell.checks?.[i] ?? null;
          return (
            <li key={n} className={`claim ${focus?.claim === n ? "focused" : ""}`}>
              <button type="button" className="claim-text" onClick={() => onFocus({ claim: n, citation: null })}>
                <span className="claim-no">{n}</span>
                <span>{claim}</span>
              </button>
              {check && (
                <div className={`claim-check ${check.ok ? "ok" : "warn"}`}>
                  {check.ok ? "Supported by its quotes" : `Not supported: ${check.reason}`}
                </div>
              )}
              {quotes.length > 0 ? (
                <ol className="citations">{quotes.map((c) => item(c, n))}</ol>
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

function CitationItem(props: { citation: Citation; active: boolean; onSelect: () => void }) {
  const { citation: c, active, onSelect } = props;
  const located = c.char_start !== null;
  return (
    <li
      className={`citation ${c.verified ? "" : "unverified"} ${active ? "active" : ""} ${located ? "clickable" : ""}`}
      onClick={() => located && onSelect()}
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

/** The URL already points at what is open; copy it to share. */
export function CopyLink() {
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
