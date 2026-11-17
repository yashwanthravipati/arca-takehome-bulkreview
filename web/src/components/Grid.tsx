import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";
import type { CellSummary, Question, ReviewGrid, Row } from "../api";
import { GridCell } from "./GridCell";
import type { Filter } from "./ReviewPage";

type GridRow = Row & { cells: Record<number, CellSummary> };

export function Grid(props: {
  grid: ReviewGrid;
  filter: Filter;
  activeCellId: number | null;
  onOpen: (cellId: number) => void;
}) {
  const { grid, filter, activeCellId, onOpen } = props;
  const status = filter === "all" ? null : filter;

  // One row per document; a filter keeps documents with at least one matching cell.
  const data = useMemo(() => {
    const byDoc = new Map<string, Record<number, CellSummary>>();
    for (const c of grid.cells) {
      const key = `${c.doc_id}@${c.version}`;
      if (!byDoc.has(key)) byDoc.set(key, {});
      byDoc.get(key)![c.column_id] = c;
    }
    const rows: GridRow[] = grid.rows.map((r) => ({ ...r, cells: byDoc.get(`${r.doc_id}@${r.version}`) ?? {} }));
    return status ? rows.filter((r) => Object.values(r.cells).some((c) => c.status === status)) : rows;
  }, [grid.rows, grid.cells, status]);

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
          header: () => <QuestionHeader q={q} />,
          cell: ({ row }) => {
            const cell = row.original.cells[q.id];
            return (
              <GridCell
                cell={cell}
                columnKey={q.key}
                active={cell !== undefined && cell.id === activeCellId}
                dimmed={status !== null && cell?.status !== status}
                onOpen={onOpen}
              />
            );
          },
        }),
      ),
    ],
    [grid.columns, activeCellId, status, onOpen],
  );

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (r) => `${r.doc_id}@${r.version}`,
  });

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
      {data.length === 0 && <div className="empty">No documents have a cell with this status.</div>}
    </div>
  );
}

function QuestionHeader({ q }: { q: Question }) {
  return (
    <div className="q-head" title={`${q.question}${q.instructions ? `\n\n${q.instructions}` : ""}`}>
      <span className="q-key">{q.key.replace(/_/g, " ")}</span>
      <span className="q-text">{q.question}</span>
      <span className="q-meta">
        {q.answer_type} · {q.model_tier}
      </span>
    </div>
  );
}
