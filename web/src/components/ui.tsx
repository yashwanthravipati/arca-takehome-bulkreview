// Small shared pieces: modal, status badge, progress bar, number formatting.
import { useEffect, type ReactNode } from "react";
import type { Status } from "../api";

export const STATUS_LABEL: Record<Status, string> = {
  done: "Done",
  not_found: "Not found",
  needs_review: "Needs review",
  failed: "Failed",
  running: "Running",
  pending: "Pending",
  held: "Not run",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

export function Progress({ value, total }: { value: number; total: number }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Modal(props: { title: string; onClose: () => void; wide?: boolean; children: ReactNode }) {
  const { title, onClose, wide, children } = props;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
export const fmtCount = (n: number) => compact.format(n);
export const fmtInt = (n: number) => n.toLocaleString("en");
