import { useEffect, useRef, type ReactNode } from "react";
import type { Citation, DocumentText, Page } from "../api";

type Located = Citation & { char_start: number; char_end: number };

/** The stored document text, page by page, with every cited span highlighted. */
export function DocumentViewer(props: { doc: DocumentText; citations: Citation[]; active: number | null }) {
  const { doc, citations, active } = props;
  const marks = useRef(new Map<number, HTMLElement>());
  const located = citations.filter((c): c is Located => c.char_start !== null && c.char_end !== null);

  // Scroll to the selected citation, or to the first one when the cell opens.
  const target = active ?? located[0]?.id ?? null;
  useEffect(() => {
    if (target === null) return;
    marks.current.get(target)?.scrollIntoView({ block: "center", behavior: active === null ? "auto" : "smooth" });
  }, [target, active, doc]);

  return (
    <div className="doc-viewer">
      <div className="doc-head">
        <div className="doc-name">{doc.title}</div>
        <div className="muted small">
          {doc.id} v{doc.version} · {doc.product} · effective {doc.effective_date ?? "–"}
          {!doc.is_current && " · superseded"}
        </div>
      </div>
      {doc.pages.map((page) => (
        <section key={page.page_no} className="doc-page">
          <div className="page-divider">
            <span>Page {page.page_no}</span>
          </div>
          <p className="page-text">{renderPage(doc.text, page, located, active, marks.current)}</p>
        </section>
      ))}
    </div>
  );
}

// Split the page at every citation boundary; each piece is plain text or a <mark> covering
// the citations that span it. The first piece of each citation registers it for scrolling.
function renderPage(text: string, page: Page, citations: Located[], active: number | null, marks: Map<number, HTMLElement>) {
  const { char_start: start, char_end: end } = page;
  const onPage = citations.filter((c) => c.char_start < end && c.char_end > start);
  const cuts = new Set([start, end]);
  for (const c of onPage) {
    cuts.add(Math.max(start, c.char_start));
    cuts.add(Math.min(end, c.char_end));
  }
  const points = [...cuts].sort((a, b) => a - b);

  const out: ReactNode[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    const pieces = withLineBreaks(text.slice(a, b), a);
    const covering = onPage.filter((c) => c.char_start <= a && c.char_end >= b);
    if (covering.length === 0) {
      out.push(...pieces);
      continue;
    }
    const startingHere = covering.filter((c) => Math.max(start, c.char_start) === a);
    const kind = covering.every((c) => c.verified) ? "verified" : "unverified";
    out.push(
      <mark
        key={a}
        className={`cite ${kind} ${covering.some((c) => c.id === active) ? "active" : ""}`}
        ref={(el) => {
          if (el) startingHere.forEach((c) => marks.set(c.id, el));
        }}
      >
        {pieces}
      </mark>,
    );
  }
  return out;
}

// The stored text has its whitespace collapsed, so Markdown headings and table rows run
// together. For reading only, the space before a heading and between table rows is shown
// as a line break; pieces are still sliced from the stored text by offset, so citation
// positions are unaffected.
const BREAK = /(?<=\|) (?=\|)| (?=#{1,6} )/;
function withLineBreaks(text: string, offset: number): ReactNode[] {
  return text.split(BREAK).flatMap((part, i) => (i === 0 ? [part] : [<br key={`${offset}-${i}`} />, part]));
}
