import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Citation, DocumentText, Page } from "../api";

type Located = Citation & { char_start: number; char_end: number };

/** The stored document as pages, every quote highlighted and tagged with its claim's number.
 * The focused claim's quotes stand out and the others are dimmed; the active quote is scrolled
 * into view. By default only cited pages are shown, with runs of other pages folded into one
 * row that expands them. */
export function DocumentPages(props: {
  doc: DocumentText;
  citations: Citation[];
  claims: string[] | null;
  focusClaim: number | null;
  active: number | null;
}) {
  const { doc, citations, claims, focusClaim, active } = props;
  const [allPages, setAllPages] = useState(false);
  const marks = useRef(new Map<number, HTMLElement>());
  const located = citations.filter((c): c is Located => c.char_start !== null && c.char_end !== null);
  const cited = new Set(
    doc.pages
      .filter((p) => located.some((c) => c.char_start < p.char_end && c.char_end > p.char_start))
      .map((p) => p.page_no),
  );
  const fold = !allPages && cited.size > 0;

  // Scroll to the active quote: at once when the view opens, smoothly after that.
  const target = active ?? located[0]?.id ?? null;
  const opened = useRef(false);
  useEffect(() => {
    if (target === null) return;
    marks.current.get(target)?.scrollIntoView({ block: "center", behavior: opened.current ? "smooth" : "auto" });
    opened.current = true;
  }, [target, doc, allPages]);

  const blocks: ReactNode[] = [];
  let folded: Page[] = [];
  const flush = () => {
    if (folded.length === 0) return;
    const [a, b] = [folded[0].page_no, folded[folded.length - 1].page_no];
    blocks.push(
      <button key={`fold-${a}`} className="pages-folded" onClick={() => setAllPages(true)}>
        {a === b ? `Page ${a}` : `Pages ${a}–${b}`} · nothing cited · show
      </button>,
    );
    folded = [];
  };
  for (const page of doc.pages) {
    if (fold && !cited.has(page.page_no)) {
      folded.push(page);
      continue;
    }
    flush();
    blocks.push(
      <section key={page.page_no} className="page-card" aria-label={`Page ${page.page_no}`}>
        <div className="page-no">
          Page {page.page_no} of {doc.pages.length}
        </div>
        <div className="page-text">{renderPage(doc.text, page, located, claims, focusClaim, active, marks.current)}</div>
      </section>,
    );
  }
  flush();

  return (
    <div className="doc-pane">
      <div className="doc-toolbar">
        <div className="doc-toolbar-title">
          <div className="doc-name">{doc.title}</div>
          <div className="muted small">
            {doc.id} v{doc.version} · {doc.product} · effective {doc.effective_date ?? "–"}
            {!doc.is_current && " · superseded"}
          </div>
        </div>
        {cited.size > 0 && (
          <label className="check small">
            <input type="checkbox" checked={!allPages} onChange={(e) => setAllPages(!e.target.checked)} />
            Cited pages only
          </label>
        )}
      </div>
      {blocks}
    </div>
  );
}

// Split the page at every citation boundary; each piece is plain text or a <mark> covering
// the citations that span it. The first piece of each citation registers it for scrolling and
// carries its claim number; hovering a mark shows the claims it supports.
function renderPage(
  text: string,
  page: Page,
  citations: Located[],
  claims: string[] | null,
  focusClaim: number | null,
  active: number | null,
  marks: Map<number, HTMLElement>,
) {
  const { char_start: start, char_end: end } = page;
  const onPage = citations.filter((c) => c.char_start < end && c.char_end > start);
  const cuts = new Set([start, end]);
  for (const c of onPage) {
    cuts.add(Math.max(start, c.char_start));
    cuts.add(Math.min(end, c.char_end));
  }
  const points = [...cuts].sort((a, b) => a - b);

  const out: ReactNode[] = [];
  const md = { bold: false }; // carried across pieces, since a highlight can start inside bold text
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    const pieces = readable(text.slice(a, b), a, md);
    const covering = onPage.filter((c) => c.char_start <= a && c.char_end >= b);
    if (covering.length === 0) {
      out.push(...pieces);
      continue;
    }
    const startingHere = covering.filter((c) => Math.max(start, c.char_start) === a);
    const kind = covering.every((c) => c.verified) ? "verified" : "unverified";
    const focus = focusClaim === null ? "" : covering.some((c) => c.claim_no === focusClaim) ? "focus" : "dim";
    const claimNos = (cs: Located[]) => [...new Set(cs.map((c) => c.claim_no))].filter((n) => n !== null);
    const tags = claimNos(startingHere);
    const title = claims ? claimNos(covering).map((n) => `${n}. ${claims[n - 1] ?? ""}`).join("\n") : "";
    out.push(
      <mark
        key={a}
        className={`cite ${kind} ${focus} ${covering.some((c) => c.id === active) ? "active" : ""}`}
        title={title || undefined}
        ref={(el) => startingHere.forEach((c) => (el ? marks.set(c.id, el) : marks.delete(c.id)))}
      >
        {tags.length > 0 && <sup className="claim-tag">{tags.join(",")}</sup>}
        {pieces}
      </mark>,
    );
  }
  return out;
}

// The stored text is Markdown with its whitespace collapsed, so headings and table rows run
// together. For reading only: the space before a heading or table row becomes a line break,
// "**" markers are hidden and the text between them is bold, and heading markers and table
// rules are muted. Pieces are still sliced from the stored text by offset, so citation
// positions are unaffected.
const TOKEN = /(\*\*|(?<=\|) (?=\|)| (?=#{1,6} ))/;
const MARKUP = /^(?:#{1,6} |\|(?:\s*:?-+:?\s*\|)+$)/;
function readable(text: string, offset: number, md: { bold: boolean }): ReactNode[] {
  const out: ReactNode[] = [];
  text.split(TOKEN).forEach((part, i) => {
    const key = `${offset}-${i}`;
    if (part === "**") {
      md.bold = !md.bold;
    } else if (i % 2 === 1) {
      out.push(<br key={key} />);
      md.bold = false; // an unclosed "**" never runs past the end of its line
    } else if (part) {
      const markup = part.match(MARKUP)?.[0] ?? "";
      const rest = part.slice(markup.length);
      if (markup) out.push(<span key={`${key}m`} className="md-mark">{markup}</span>);
      if (rest) out.push(md.bold ? <strong key={key}>{rest}</strong> : rest);
    }
  });
  return out;
}
