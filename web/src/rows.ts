// Which documents the grid shows, and in what order: status chips, a search over answers,
// one value condition on a column, and sorting by a column's answers. All of it works on the
// cell summaries the grid already has (status and the one-line answer), so it needs no API.
import type { AnswerType, CellSummary, Question, ReviewGrid, Row, Status } from "./api";

export type GridRow = Row & { cells: Record<number, CellSummary> };
export type StatusFilter = "all" | Extract<Status, "needs_review" | "not_found" | "failed" | "held">;

export interface Condition {
  id: string;
  label: string;
  test: (cell: CellSummary | undefined) => boolean;
}
export interface Where {
  columnId: number;
  condition: string;
}
export interface Sort {
  columnId: number;
  desc: boolean;
}
export interface View {
  status: StatusFilter;
  search: string;
  where: Where | null;
  sort: Sort | null;
}
export const EMPTY_VIEW: View = { status: "all", search: "", where: null, sort: null };

const answered = (c: CellSummary | undefined): c is CellSummary & { short: string } =>
  (c?.status === "done" || c?.status === "needs_review") && c.short !== null;

/** Today as YYYY-MM-DD in local time, comparable with date answers as strings. */
export const today = () => new Date().toLocaleDateString("sv-SE");

/** A list answer's items: `items` from the API, else split from `short` (an older API that only
 * joins them with "; ", sending "(none)" for an empty list). */
export const listItems = (cell: CellSummary & { short: string }) =>
  cell.items ?? (cell.short === "(none)" ? [] : cell.short.split("; "));

/** The value conditions a column offers, by answer type. */
export function conditionsFor(type: AnswerType): Condition[] {
  const common: Condition[] = [
    { id: "answered", label: "has an answer", test: answered },
    { id: "not_found", label: "not found", test: (c) => c?.status === "not_found" },
  ];
  const typed: Record<AnswerType, Condition[]> = {
    boolean: [
      { id: "yes", label: "is yes", test: (c) => answered(c) && c.short === "yes" },
      { id: "no", label: "is no", test: (c) => answered(c) && c.short === "no" },
    ],
    date: [
      { id: "past", label: "is before today", test: (c) => answered(c) && c.short < today() },
      { id: "upcoming", label: "is today or later", test: (c) => answered(c) && c.short >= today() },
    ],
    list: [
      { id: "some", label: "has items", test: (c) => answered(c) && listItems(c).length > 0 },
      { id: "none", label: "is empty", test: (c) => answered(c) && listItems(c).length === 0 },
    ],
    text: [],
  };
  return [...typed[type], ...common];
}

/** Sort key: answered cells first (by value), then not found, then everything unfinished. */
function sortKey(cell: CellSummary | undefined, type: AnswerType): [number, number | string] {
  if (!answered(cell)) return [cell?.status === "not_found" ? 1 : 2, 0];
  const s = cell.short;
  if (type === "boolean") return [0, s === "yes" ? 1 : 0];
  if (type === "list") return [0, listItems(cell).length];
  if (type === "date") return [0, s];
  return [0, s.toLowerCase()];
}

export function matchesSearch(cell: CellSummary | undefined, query: string) {
  return !!query && !!cell?.short?.toLowerCase().includes(query);
}

/** The grid's rows after the view's filters and sort. */
export function viewRows(grid: ReviewGrid, view: View): GridRow[] {
  const byDoc = new Map<string, Record<number, CellSummary>>();
  for (const c of grid.cells) {
    const key = `${c.doc_id}@${c.version}`;
    if (!byDoc.has(key)) byDoc.set(key, {});
    byDoc.get(key)![c.column_id] = c;
  }
  let rows: GridRow[] = grid.rows.map((r) => ({ ...r, cells: byDoc.get(`${r.doc_id}@${r.version}`) ?? {} }));

  if (view.status !== "all") {
    rows = rows.filter((r) => Object.values(r.cells).some((c) => c.status === view.status));
  }
  const query = view.search.trim().toLowerCase();
  if (query) {
    rows = rows.filter(
      (r) =>
        `${r.doc_id} ${r.title} ${r.product}`.toLowerCase().includes(query) ||
        Object.values(r.cells).some((c) => matchesSearch(c, query)),
    );
  }
  const where = view.where && grid.columns.find((q) => q.id === view.where!.columnId);
  const condition = where && conditionsFor(where.answer_type).find((c) => c.id === view.where!.condition);
  if (where && condition) rows = rows.filter((r) => condition.test(r.cells[where.id]));

  const sortBy = view.sort && grid.columns.find((q) => q.id === view.sort!.columnId);
  if (sortBy) rows = sortRows(rows, sortBy, view.sort!.desc);
  return rows;
}

function sortRows(rows: GridRow[], q: Question, desc: boolean) {
  const keyed = rows.map((r) => ({ r, k: sortKey(r.cells[q.id], q.answer_type) }));
  keyed.sort((a, b) => {
    if (a.k[0] !== b.k[0]) return a.k[0] - b.k[0]; // unanswered stay last in both directions
    const [x, y] = [a.k[1], b.k[1]];
    const cmp = x < y ? -1 : x > y ? 1 : 0;
    if (cmp) return desc ? -cmp : cmp;
    return a.r.doc_id.localeCompare(b.r.doc_id) || a.r.version.localeCompare(b.r.version);
  });
  return keyed.map(({ r }) => r);
}

/** Whether any filter is on (the sort alone doesn't hide documents). */
export const isFiltered = (v: View) => v.status !== "all" || v.search.trim() !== "" || v.where !== null;
