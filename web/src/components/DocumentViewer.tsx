import { useEffect, useRef, type ReactNode } from "react";
import type { Citation, DocumentText, Page } from "../api";

type Located = Citation & { char_start: number; char_end: number };

/** The stored document text, page by page, with every cited span highlighted and tagged with
 * the number of the claim it supports. */
export function DocumentViewer(props: {
  doc: DocumentText;
  citations: Citation[];
  claims?: string[] | null;
  active: number | null;
}) {
  const { doc, citations, claims = null, active } = props;
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
          <p className="page-text">{renderPage(doc.text, page, located, claims, active, marks.current)}</p>
        </section>
      ))}
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
    const claimNos = (cs: Located[]) => [...new Set(cs.map((c) => c.claim_no))].filter((n) => n !== null);
    const tags = claimNos(startingHere);
    const title = claims ? claimNos(covering).map((n) => `${n}. ${claims[n - 1] ?? ""}`).join("\n") : "";
    out.push(
      <mark
        key={a}
        className={`cite ${kind} ${covering.some((c) => c.id === active) ? "active" : ""}`}
        title={title || undefined}
        ref={(el) => {
          if (el) startingHere.forEach((c) => marks.set(c.id, el));
        }}
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
