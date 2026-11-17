import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo } from "react";
import type { Question, ReviewGrid } from "../api";
import { matchesSearch, type GridRow, type Sort, type View } from "../rows";
import { GridCell } from "./GridCell";

/** One row per document, one column per question. `rows` are already filtered and sorted. */
export function Grid(props: {
  grid: ReviewGrid;
  rows: GridRow[];
  view: View;
  activeCellId: number | null;
  onOpen: (cellId: number) => void;
  onSort: (sort: Sort | null) => void;
  onEdit: (column: Question) => void;
  onRunHeld: (column: Question) => void;
}) {
  const { grid, rows, view, activeCellId, onOpen, onSort, onEdit, onRunHeld } = props;
  const status = view.status === "all" ? null : view.status;
  const query = view.search.trim().toLowerCase();

  // Cells per column that were left out of a sample run.
  const held = useMemo(() => {
    const n = new Map<number, number>();
    for (const c of grid.cells) if (c.status === "held") n.set(c.column_id, (n.get(c.column_id) ?? 0) + 1);
    return n;
  }, [grid.cells]);

  const columns = useMemo<ColumnDef<GridRow>[]>(
    () => [
      {
        id: "document",
        header: "Document",
        cell: ({ row }) => (
          <div className="doc-label" title={row.original.title}>
            <span className="doc-id">
              {row.original.doc_id} <span className="muted">v{row.original.version}</span>
            </span>
            <span className="doc-title">{row.original.title}</span>
          </div>
        ),
      },
      ...grid.columns.map(
        (q): ColumnDef<GridRow> => ({
          id: String(q.id),
          header: () => (
            <QuestionHeader
              q={q}
              sort={view.sort}
              held={held.get(q.id) ?? 0}
              onSort={onSort}
              onEdit={onEdit}
              onRunHeld={onRunHeld}
            />
          ),
          cell: ({ row }) => {
            const cell = row.original.cells[q.id];
            return (
              <GridCell
                cell={cell}
                question={q}
                active={cell !== undefined && cell.id === activeCellId}
                dimmed={status !== null && cell?.status !== status}
                match={matchesSearch(cell, query)}
                onOpen={onOpen}
              />
            );
          },
        }),
      ),
    ],
    [grid.columns, activeCellId, status, query, view.sort, held, onOpen, onSort, onEdit, onRunHeld],
  );

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (r) => `${r.doc_id}@${r.version}`,
  });

  // Bring the open cell into view, e.g. when the page was opened from a link to it.
  useEffect(() => {
    if (activeCellId === null) return;
    document.querySelector(`[data-cell-id="${activeCellId}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeCellId]);

  return (
    <div className="grid-scroll">
      <table className="grid">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((h) => (
                <th key={h.id} className={h.column.id === "document" ? "doc-col" : undefined}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((c) => (
                <td key={c.id} className={c.column.id === "document" ? "doc-col" : undefined}>
                  {flexRender(c.column.columnDef.cell, c.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="empty">No documents match these filters.</div>}
    </div>
  );
}

function QuestionHeader(props: {
  q: Question;
  sort: Sort | null;
  held: number;
  onSort: (sort: Sort | null) => void;
  onEdit: (column: Question) => void;
  onRunHeld: (column: Question) => void;
}) {
  const { q, sort, held, onSort, onEdit, onRunHeld } = props;
  const dir = sort?.columnId === q.id ? (sort.desc ? "desc" : "asc") : null;
  // Click to sort ascending, again for descending, a third time to stop sorting.
  const next = () => onSort(dir === null ? { columnId: q.id, desc: false } : dir === "asc" ? { columnId: q.id, desc: true } : null);
  return (
    <div className="q-head" title={`${q.question}${q.instructions ? `\n\n${q.instructions}` : ""}`}>
      <div className="q-top">
        <button className={`q-key sort-btn ${dir ? "sorted" : ""}`} onClick={next} aria-label={`Sort by ${q.key}`}>
          {q.key.replace(/_/g, " ")}
          <span className="sort-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : dir === "desc" ? "▼" : "↕"}
          </span>
        </button>
        <button className="link-btn small" onClick={() => onEdit(q)}>
          Edit
        </button>
      </div>
      <span className="q-text">{q.question}</span>
      <span className="q-meta">
        {q.answer_type} · {q.model_tier}
      </span>
      {held > 0 && (
        <button className="btn small run-rest" onClick={() => onRunHeld(q)}>
          Run on remaining {held}
        </button>
      )}
    </div>
  );
}
