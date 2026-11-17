import { useMemo, useState } from "react";
import { api, type Question } from "../api";
import { EMPTY_VIEW, conditionsFor, isFiltered, viewRows, type StatusFilter, type View, type Where } from "../rows";
import { useReview } from "../useReview";
import { NO_FOCUS, type Focus } from "./answer";
import { CellDrawer } from "./CellDrawer";
import { EvidenceView } from "./EvidenceView";
import { ColumnModal } from "./ColumnModal";
import { Grid } from "./Grid";
import { ReviewHeader } from "./ReviewHeader";

const STATUS_FILTERS: [StatusFilter, string][] = [
  ["all", "All"],
  ["needs_review", "Needs review"],
  ["not_found", "Not found"],
  ["failed", "Failed"],
  ["held", "Not run"],
];

type Editing = { column: Question | null } | null; // column null: adding a new one

/** One review's grid. `cellId` (from the URL) is the cell whose drawer is open; with
 * `evidence`, its evidence view is open on top. */
export function ReviewPage({ id, cellId, evidence }: { id: number; cellId: number | null; evidence: boolean }) {
  const { grid, error, live, reload } = useReview(id);
  const [view, setView] = useState<View>(EMPTY_VIEW);
  const [editing, setEditing] = useState<Editing>(null);
  const [focus, setFocus] = useState<Focus>(NO_FOCUS); // where the evidence view opens
  const rows = useMemo(() => (grid ? viewRows(grid, view) : []), [grid, view]);
  const allRows = useMemo(() => (grid ? viewRows(grid, EMPTY_VIEW) : []), [grid]);

  if (error) return <main className="page"><div className="error">Could not load review {id}: {error}</div></main>;
  if (!grid) return <main className="page muted">Loading review…</main>;

  // The open cell lives in the URL, so a link opens the same answer for whoever follows it.
  const open = (cell: number | null) => {
    window.location.hash = cell === null ? `#/reviews/${id}` : `#/reviews/${id}/cells/${cell}`;
  };
  const runHeld = (q: Question) =>
    api.runHeld(id, q.id).catch((e: Error) => window.alert(`Could not run the rest of "${q.key}": ${e.message}`));
  const openCell = grid.cells.find((c) => c.id === cellId);
  const update = (patch: Partial<View>) => setView((v) => ({ ...v, ...patch }));
  const openEvidence = (cell: number, at: Focus = NO_FOCUS) => {
    setFocus(at);
    window.location.hash = `#/reviews/${id}/cells/${cell}/evidence`;
  };

  // The evidence view's previous and next documents: the same column, in the grid's current
  // order (all documents if the open one is filtered out).
  const order = (() => {
    if (!openCell) return [];
    const ids = (shown: typeof rows) => shown.flatMap((r) => r.cells[openCell.column_id]?.id ?? []);
    const visible = ids(rows);
    return visible.includes(openCell.id) ? visible : ids(allRows);
  })();
  const at = openCell ? order.indexOf(openCell.id) : -1;
  const step = (delta: number) => {
    const next = order[at + delta];
    return at >= 0 && next !== undefined ? () => openEvidence(next) : null;
  };

  return (
    <div className="review-page">
      <ReviewHeader grid={grid} live={live} onAddColumn={() => setEditing({ column: null })} />
      <div className="chips toolbar">
        {STATUS_FILTERS.filter(([value]) => value !== "held" || grid.totals.counts.held > 0).map(([value, label]) => (
          <button key={value} className={`chip ${view.status === value ? "on" : ""}`} onClick={() => update({ status: value })}>
            {label}
            {value !== "all" && <span className="chip-count">{grid.totals.counts[value] ?? 0}</span>}
          </button>
        ))}
        <span className="toolbar-sep" />
        <input
          className="search"
          type="search"
          placeholder="Search answers and documents"
          value={view.search}
          onChange={(e) => update({ search: e.target.value })}
        />
        <WhereFilter columns={grid.columns} where={view.where} onChange={(where) => update({ where })} />
        <span className="toolbar-spacer" />
        <span className="muted small tabular">
          {isFiltered(view) ? `${rows.length} of ${grid.rows.length} documents` : `${grid.rows.length} documents`}
        </span>
        {(isFiltered(view) || view.sort) && (
          <button className="link-btn" onClick={() => setView(EMPTY_VIEW)}>
            Clear
          </button>
        )}
      </div>
      <div className="workspace">
        <Grid
          grid={grid}
          rows={rows}
          view={view}
          activeCellId={cellId}
          onOpen={open}
          onSort={(sort) => update({ sort })}
          onEdit={(column) => setEditing({ column })}
          onRunHeld={runHeld}
        />
        {openCell && (
          <CellDrawer
            key={openCell.id}
            summary={openCell}
            onClose={() => open(null)}
            onOpenEvidence={(at) => openEvidence(openCell.id, at)}
          />
        )}
      </div>
      {openCell && evidence && (
        <EvidenceView
          key={openCell.id}
          summary={openCell}
          initialFocus={focus}
          position={at >= 0 ? { index: at, total: order.length } : null}
          onPrev={step(-1)}
          onNext={step(1)}
          onClose={() => {
            setFocus(NO_FOCUS);
            open(openCell.id);
          }}
        />
      )}
      {editing && (
        <ColumnModal
          reviewId={id}
          column={editing.column}
          documents={grid.rows.length}
          existingKeys={grid.columns.map((c) => c.key)}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload(); // new or changed column definitions; cell changes arrive on the live stream
          }}
        />
      )}
    </div>
  );
}

/** "Filter by column" then a condition that fits the column's answer type. */
function WhereFilter(props: { columns: Question[]; where: Where | null; onChange: (w: Where | null) => void }) {
  const { columns, where, onChange } = props;
  const column = columns.find((q) => q.id === where?.columnId);
  return (
    <span className="where">
      <select
        className="mini-select"
        aria-label="Filter by column"
        value={column?.id ?? ""}
        onChange={(e) => {
          const q = columns.find((c) => c.id === Number(e.target.value));
          onChange(q ? { columnId: q.id, condition: conditionsFor(q.answer_type)[0].id } : null);
        }}
      >
        <option value="">Filter by column…</option>
        {columns.map((q) => (
          <option key={q.id} value={q.id}>
            {q.key.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      {column && where && (
        <select
          className="mini-select"
          aria-label="Condition"
          value={where.condition}
          onChange={(e) => onChange({ columnId: column.id, condition: e.target.value })}
        >
          {conditionsFor(column.answer_type).map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      )}
    </span>
  );
}
