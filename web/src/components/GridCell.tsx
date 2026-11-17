import type { CellSummary } from "../api";

export function GridCell(props: {
  cell: CellSummary | undefined;
  columnKey: string;
  active: boolean;
  dimmed: boolean;
  onOpen: (cellId: number) => void;
}) {
  const { cell, columnKey, active, dimmed, onOpen } = props;
  if (!cell) return <div className="cell cell-empty" />;

  const classes = ["cell", `cell-${cell.status}`, active && "active", dimmed && "dimmed"];
  return (
    <button
      className={classes.filter(Boolean).join(" ")}
      onClick={() => onOpen(cell.id)}
      title={cell.short ?? undefined}
      data-doc={cell.doc_id}
      data-key={columnKey}
    >
      <CellContent cell={cell} />
    </button>
  );
}

function CellContent({ cell }: { cell: CellSummary }) {
  switch (cell.status) {
    case "pending":
      return <span className="shimmer" aria-label="Pending" />;
    case "running":
      return (
        <span className="running">
          <span className="spinner" /> Running
        </span>
      );
    case "not_found":
      return <span className="not-found">Not found</span>;
    case "failed":
      return <span className="failed-label">Failed</span>;
    case "needs_review":
      return (
        <>
          <span className="answer">{cell.short}</span>
          <span className="review-tag">Review</span>
        </>
      );
    case "done":
      return (
        <>
          <span className="answer">{cell.short}</span>
          {cell.page !== null && <span className="page-tag">p.{cell.page}</span>}
        </>
      );
  }
}
