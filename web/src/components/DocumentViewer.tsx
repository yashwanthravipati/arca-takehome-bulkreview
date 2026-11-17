import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import type { Citation, DocumentText, Page } from "../api";

type Located = Citation & { char_start: number; char_end: number };

/** What highlighting needs while a document renders: the quotes, which claim and quote are in
 * focus, where each quote's first highlight registers for scrolling, and which quotes already
 * have one (a quote across several lines or table cells gets its claim number only once). */
interface Ctx {
  text: string;
  citations: Located[];
  claims: string[] | null;
  focusClaim: number | null;
  active: number | null;
  marks: Map<number, HTMLElement>;
  seen: Set<number>;
}

/** The stored document as pages, every quote highlighted and tagged with its claim's number.
 * The focused claim's quotes stand out and the others are dimmed; the active quote is scrolled
 * into view. By default only cited pages are shown, with runs of other pages folded into one
 * row that expands them. Headings, paragraphs, lists and tables are rebuilt from the
 * document's layout (where each source line starts); everything is still sliced from the
 * stored text by offset, so highlights line up with the citations exactly. */
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

  const ctx: Ctx = { text: doc.text, citations: located, claims, focusClaim, active, marks: marks.current, seen: new Set() };
  const layout = doc.line_starts && doc.block_starts ? { starts: doc.line_starts, blocks: new Set(doc.block_starts) } : null;

  const shown: ReactNode[] = [];
  let folded: Page[] = [];
  const flush = () => {
    if (folded.length === 0) return;
    const [a, b] = [folded[0].page_no, folded[folded.length - 1].page_no];
    shown.push(
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
    shown.push(
      <section key={page.page_no} className="page-card" aria-label={`Page ${page.page_no}`}>
        <div className="page-no">
          Page {page.page_no} of {doc.pages.length}
        </div>
        <div className="page-text">
          {layout ? renderBlocks(page, layout, ctx) : renderRange(page.char_start, page.char_end, ctx, true)}
        </div>
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
      {shown}
    </div>
  );
}

// --- page structure ---------------------------------------------------------------------

type Line = { start: number; end: number; blockStart: boolean };
type Block =
  | { kind: "heading"; level: number; line: Line; from: number }
  | { kind: "table"; rows: Line[]; header: boolean }
  | { kind: "list"; items: { line: Line; from: number }[] }
  | { kind: "para"; lines: Line[] };

const HEADING = /^(#{1,6}) /;
const BULLET = /^[-*] /;
const TABLE_RULE = /^\|(?:\s*:?-+:?\s*\|)+$/;
const HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

/** A page's source lines grouped into blocks. A line starting with "#" is a heading, "|" a
 * table row ("|---|" after the first row makes it the header), "- " a list item; other lines
 * are paragraph lines. A blank line in the source (a block start) ends a list, table or
 * paragraph. */
function pageBlocks(text: string, page: Page, layout: { starts: number[]; blocks: Set<number> }): Block[] {
  const starts = layout.starts.filter((s) => s >= page.char_start && s < page.char_end);
  const blocks: Block[] = [];
  starts.forEach((start, i) => {
    // A line ends just before the space that joins it to the next one.
    const line = { start, end: i + 1 < starts.length ? starts[i + 1] - 1 : page.char_end, blockStart: layout.blocks.has(start) };
    const t = text.slice(line.start, line.end);
    const last = blocks.at(-1);
    const continues = (kind: Block["kind"]) => last?.kind === kind && !line.blockStart;
    const heading = HEADING.exec(t);
    const bullet = BULLET.exec(t);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, line, from: start + heading[0].length });
    } else if (t.startsWith("|")) {
      if (TABLE_RULE.test(t)) {
        if (last?.kind === "table" && last.rows.length === 1) last.header = true;
      } else if (last?.kind === "table" && continues("table")) {
        last.rows.push(line);
      } else {
        blocks.push({ kind: "table", rows: [line], header: false });
      }
    } else if (bullet) {
      const item = { line, from: start + bullet[0].length };
      if (last?.kind === "list" && continues("list")) last.items.push(item);
      else blocks.push({ kind: "list", items: [item] });
    } else if (last?.kind === "para" && continues("para")) {
      last.lines.push(line);
    } else {
      blocks.push({ kind: "para", lines: [line] });
    }
  });
  return blocks;
}

/** A table row's cells: the text between its pipes, trimmed of the spaces around it. */
function rowCells(text: string, row: Line): [number, number][] {
  const cells: [number, number][] = [];
  let from = text[row.start] === "|" ? row.start + 1 : row.start;
  for (let i = from; i <= row.end; i++) {
    if (i < row.end && text[i] !== "|") continue;
    let [a, b] = [from, i];
    while (a < b && text[a] === " ") a++;
    while (b > a && text[b - 1] === " ") b--;
    if (i < row.end || a < b) cells.push([a, b]); // skip the empty piece after a closing pipe
    from = i + 1;
  }
  return cells;
}

function renderBlocks(page: Page, layout: { starts: number[]; blocks: Set<number> }, ctx: Ctx): ReactNode[] {
  const range = (a: number, b: number) => renderRange(a, b, ctx, false);
  return pageBlocks(ctx.text, page, layout).map((block) => {
    switch (block.kind) {
      case "heading": {
        const Tag = HEADING_TAGS[block.level - 1];
        return <Tag key={block.line.start}>{range(block.from, block.line.end)}</Tag>;
      }
      case "list":
        return (
          <ul key={block.items[0].line.start}>
            {block.items.map((item) => (
              <li key={item.line.start}>{range(item.from, item.line.end)}</li>
            ))}
          </ul>
        );
      case "para":
        return (
          <p key={block.lines[0].start}>
            {block.lines.map((line, i) => (
              <Fragment key={line.start}>
                {i > 0 && <br />}
                {range(line.start, line.end)}
              </Fragment>
            ))}
          </p>
        );
      case "table": {
        const head = block.header ? block.rows[0] : null;
        const body = block.header ? block.rows.slice(1) : block.rows;
        return (
          <div key={block.rows[0].start} className="doc-table-wrap">
            <table className="doc-table">
              {head && (
                <thead>
                  <tr>
                    {rowCells(ctx.text, head).map(([a, b]) => (
                      <th key={a}>{range(a, b)}</th>
                    ))}
                  </tr>
                </thead>
              )}
              <tbody>
                {body.map((row) => (
                  <tr key={row.start}>
                    {rowCells(ctx.text, row).map(([a, b]) => (
                      <td key={a}>{range(a, b)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
    }
  });
}

// --- highlighting -----------------------------------------------------------------------

// Split [start, end) at every citation boundary; each piece is plain text or a <mark>
// covering the citations that span it. A quote's first piece in the document registers it for
// scrolling and carries its claim number; hovering a mark shows the claims it supports.
function renderRange(start: number, end: number, ctx: Ctx, breaks: boolean): ReactNode[] {
  const { text, claims, focusClaim, active, marks, seen } = ctx;
  const here = ctx.citations.filter((c) => c.char_start < end && c.char_end > start);
  const cuts = new Set([start, end]);
  for (const c of here) {
    cuts.add(Math.max(start, c.char_start));
    cuts.add(Math.min(end, c.char_end));
  }
  const points = [...cuts].sort((a, b) => a - b);

  const out: ReactNode[] = [];
  // Carried across pieces, since a highlight can start inside bold or italic text.
  const md = { bold: false, italic: false, italics: italicMarkers(text, start, end) };
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    const pieces = readable(text.slice(a, b), a, md, breaks);
    const covering = here.filter((c) => c.char_start <= a && c.char_end >= b);
    if (covering.length === 0) {
      out.push(...pieces);
      continue;
    }
    const first = covering.filter((c) => !seen.has(c.id));
    first.forEach((c) => seen.add(c.id));
    const kind = covering.every((c) => c.verified) ? "verified" : "unverified";
    const focus = focusClaim === null ? "" : covering.some((c) => c.claim_no === focusClaim) ? "focus" : "dim";
    const claimNos = (cs: Located[]) => [...new Set(cs.map((c) => c.claim_no))].filter((n) => n !== null);
    const tags = claimNos(first);
    const title = claims ? claimNos(covering).map((n) => `${n}. ${claims[n - 1] ?? ""}`).join("\n") : "";
    out.push(
      <mark
        key={a}
        className={`cite ${kind} ${focus} ${covering.some((c) => c.id === active) ? "active" : ""}`}
        title={title || undefined}
        ref={(el) => first.forEach((c) => (el ? marks.set(c.id, el) : marks.delete(c.id)))}
      >
        {tags.length > 0 && <sup className="claim-tag">{tags.join(",")}</sup>}
        {pieces}
      </mark>,
    );
  }
  return out;
}

// "**" markers are hidden and the text between them is bold; a pair of "*" around text on
// one line ("*E. coli*") is italic. Nothing else is hidden: a lone "*", like the footnote mark
// in "10*", shows as itself, and "\*" shows as "*". Without a layout (documents ingested
// before it was kept), the space before a heading or table row also becomes a line break,
// and heading markers and table rules are muted. Pieces are still sliced from the stored text
// by offset, so citation positions are unaffected.
const EMPHASIS = /(\\\*|\*\*|\*)/;
const EMPHASIS_AND_BREAKS = /(\\\*|\*\*|\*|(?<=\|) (?=\|)| (?=#{1,6} ))/;
const ITALIC = /(?<![*\\])\*(?=[^\s*])[^*]*?[^\s*\\]\*(?!\*)/g;
const MARKUP = /^(?:#{1,6} |\|(?:\s*:?-+:?\s*\|)+$)/;
type Emphasis = { bold: boolean; italic: boolean; italics: Set<number> };

/** Offsets of the "*" that open or close italics in [start, end). */
function italicMarkers(text: string, start: number, end: number): Set<number> {
  const at = new Set<number>();
  for (const m of text.slice(start, end).matchAll(ITALIC)) {
    at.add(start + m.index);
    at.add(start + m.index + m[0].length - 1);
  }
  return at;
}

function readable(text: string, offset: number, md: Emphasis, breaks: boolean): ReactNode[] {
  const out: ReactNode[] = [];
  let pos = offset;
  text.split(breaks ? EMPHASIS_AND_BREAKS : EMPHASIS).forEach((part, i) => {
    const key = `${offset}-${i}`;
    const at = pos;
    pos += part.length;
    if (part === "\\*") {
      out.push("*");
    } else if (part === "**") {
      md.bold = !md.bold;
    } else if (part === "*") {
      if (md.italics.has(at)) md.italic = !md.italic;
      else out.push("*");
    } else if (i % 2 === 1) {
      out.push(<br key={key} />);
      md.bold = md.italic = false; // an unclosed marker never runs past the end of its line
    } else if (part) {
      const markup = breaks ? (part.match(MARKUP)?.[0] ?? "") : "";
      const rest = part.slice(markup.length);
      if (markup) out.push(<span key={`${key}m`} className="md-mark">{markup}</span>);
      if (rest) {
        const node = md.italic ? <em key={`${key}i`}>{rest}</em> : rest;
        out.push(md.bold ? <strong key={key}>{node}</strong> : node);
      }
    }
  });
  return out;
}
