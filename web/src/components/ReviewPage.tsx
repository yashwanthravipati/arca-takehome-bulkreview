import { useState } from "react";
import type { Status } from "../api";
import { useReview } from "../useReview";
import { AddColumnModal } from "./AddColumnModal";
import { CellDrawer } from "./CellDrawer";
import { Grid } from "./Grid";
import { ReviewHeader } from "./ReviewHeader";

export type Filter = "all" | Extract<Status, "needs_review" | "not_found" | "failed">;
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["needs_review", "Needs review"],
  ["not_found", "Not found"],
  ["failed", "Failed"],
];

export function ReviewPage({ id }: { id: number }) {
  const { grid, error, live, reload } = useReview(id);
  const [filter, setFilter] = useState<Filter>("all");
  const [openCellId, setOpenCellId] = useState<number | null>(null);
  const [addingColumn, setAddingColumn] = useState(false);

  if (error) return <main className="page"><div className="error">Could not load review {id}: {error}</div></main>;
  if (!grid) return <main className="page muted">Loading review…</main>;

  const openCell = grid.cells.find((c) => c.id === openCellId);
  return (
    <div className="review-page">
      <ReviewHeader grid={grid} live={live} onAddColumn={() => setAddingColumn(true)} />
      <div className="chips">
        {FILTERS.map(([value, label]) => (
          <button key={value} className={`chip ${filter === value ? "on" : ""}`} onClick={() => setFilter(value)}>
            {label}
            {value !== "all" && <span className="chip-count">{grid.totals.counts[value]}</span>}
          </button>
        ))}
      </div>
      <div className="workspace">
        <Grid grid={grid} filter={filter} activeCellId={openCellId} onOpen={setOpenCellId} />
        {openCell && <CellDrawer key={openCell.id} summary={openCell} onClose={() => setOpenCellId(null)} />}
      </div>
      {addingColumn && (
        <AddColumnModal
          reviewId={id}
          existingKeys={grid.columns.map((c) => c.key)}
          onClose={() => setAddingColumn(false)}
          onAdded={() => {
            setAddingColumn(false);
            reload();
          }}
        />
      )}
    </div>
  );
}
