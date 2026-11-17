import { useCallback, useEffect, useState } from "react";
import { api, type CellSummary, type ReviewGrid, type Totals } from "./api";

/** A review's grid, kept live: one snapshot, then cell changes over Server-Sent Events. */
export function useReview(id: number) {
  const [grid, setGrid] = useState<ReviewGrid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);

  const reload = useCallback(
    () => api.review(id).then(setGrid, (e: Error) => setError(e.message)),
    [id],
  );
  useEffect(() => {
    reload();
  }, [reload]);

  // Subscribe only once the snapshot is in: the stream starts with every cell, so nothing
  // that changed between the snapshot and the subscription is missed.
  const loaded = grid !== null;
  useEffect(() => {
    if (!loaded) return;
    const source = new EventSource(api.eventsUrl(id));
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false); // the browser reconnects by itself
    source.addEventListener("cells", (event) => {
      const { cells, totals } = JSON.parse((event as MessageEvent<string>).data) as {
        cells: CellSummary[];
        totals: Totals;
      };
      setGrid((g) => g && merge(g, cells, totals));
    });
    return () => source.close();
  }, [id, loaded]);

  // A cell for a column or document we don't have yet (e.g. a column added elsewhere):
  // fetch the review's structure again.
  const stale =
    grid !== null &&
    grid.cells.some(
      (c) =>
        !grid.columns.some((q) => q.id === c.column_id) ||
        !grid.rows.some((r) => r.doc_id === c.doc_id && r.version === c.version),
    );
  useEffect(() => {
    if (stale) reload();
  }, [stale, reload]);

  return { grid, error, live, reload };
}

function merge(grid: ReviewGrid, changed: CellSummary[], totals: Totals): ReviewGrid {
  const byId = new Map(grid.cells.map((c) => [c.id, c]));
  for (const c of changed) byId.set(c.id, c);
  return { ...grid, cells: [...byId.values()], totals };
}
