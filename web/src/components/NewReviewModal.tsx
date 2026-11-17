import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, type DocFilter, type DocumentMeta, type Estimate, type Template } from "../api";
import { Modal, fmtInt } from "./ui";

/**
 * Name a review, pick its template, and choose which documents it covers: by product, current
 * versions only or all, and by unticking single documents. Before anything runs it shows the
 * cell count and a rough cost and duration from the API's estimate.
 */
export function NewReviewModal({ onClose }: { onClose: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [template, setTemplate] = useState("");
  const [allDocs, setAllDocs] = useState<DocumentMeta[]>([]);
  const [name, setName] = useState(`Stability review ${new Date().toISOString().slice(0, 10)}`);
  const [products, setProducts] = useState<string[]>([]); // none ticked: every product
  const [currentOnly, setCurrentOnly] = useState(true);
  const [excluded, setExcluded] = useState<Set<string>>(new Set()); // document ids unticked
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.templates().then((t) => {
      setTemplates(t);
      setTemplate((current) => current || t[0]?.name || "");
    }, (e: Error) => setError(e.message));
    api.documents().then(setAllDocs, (e: Error) => setError(e.message));
  }, []);

  const chosen = templates.find((t) => t.name === template);
  // Products that have documents of the template's type.
  const productOptions = useMemo(
    () => [...new Set(allDocs.filter((d) => d.doc_type === chosen?.doc_type).map((d) => d.product))].sort(),
    [allDocs, chosen?.doc_type],
  );

  // Documents the product and version conditions match (the latest estimate lists them).
  const matched = estimate?.documents ?? [];
  const selectedIds = [...new Set(matched.map((d) => d.id))].filter((id) => !excluded.has(id));
  const docFilter: DocFilter = {
    ...(products.length ? { product: products } : {}),
    is_current: currentOnly ? true : null, // null drops the template's "current versions only"
    ...(excluded.size ? { id: selectedIds } : {}),
  };
  const filterKey = JSON.stringify(docFilter);

  // Re-estimate when the selection changes; a late reply to an older selection is ignored.
  const latest = useRef(0);
  useEffect(() => {
    if (!template) return;
    const call = ++latest.current;
    const timer = setTimeout(() => {
      api.estimate(template, JSON.parse(filterKey)).then(
        (e) => call === latest.current && (setEstimate(e), setError(null)),
        (e: Error) => call === latest.current && setError(e.message),
      );
    }, 150);
    return () => clearTimeout(timer);
  }, [template, filterKey]);

  // Unticked documents are kept by id only while the product and version conditions stay put.
  const resetSelection = () => setExcluded(new Set());
  const toggleProduct = (p: string) => {
    setProducts((list) => (list.includes(p) ? list.filter((x) => x !== p) : [...list, p]));
    resetSelection();
  };
  const toggleDoc = (id: string) =>
    setExcluded((set) => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createReview(name, template, docFilter);
      window.location.hash = `#/reviews/${id}`;
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const docCount = estimate?.selected ?? 0;
  return (
    <Modal title="New review" onClose={onClose} wide>
      <form onSubmit={submit} className="form">
        <div className="form-row">
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
          </label>
          <label>
            Template
            <select
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value);
                setProducts([]);
                resetSelection();
              }}
            >
              {templates.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
        </div>
        {chosen && (
          <div className="muted small">
            {chosen.columns.length} questions: {chosen.columns.map((c) => c.key.replace(/_/g, " ")).join(", ")}.
          </div>
        )}

        <fieldset className="doc-picker">
          <legend>
            Documents <span className="muted">({chosen?.doc_type.replace(/_/g, " ") ?? "…"})</span>
          </legend>
          <div className="picker-filters">
            <span className="muted small">Products</span>
            <button type="button" className={`chip ${products.length === 0 ? "on" : ""}`} onClick={() => (setProducts([]), resetSelection())}>
              All
            </button>
            {productOptions.map((p) => (
              <button key={p} type="button" className={`chip ${products.includes(p) ? "on" : ""}`} onClick={() => toggleProduct(p)}>
                {p}
              </button>
            ))}
            <label className="check">
              <input
                type="checkbox"
                checked={currentOnly}
                onChange={(e) => {
                  setCurrentOnly(e.target.checked);
                  resetSelection();
                }}
              />
              Current versions only
            </label>
          </div>
          <ul className="doc-list">
            {matched.map((d) => (
              <li key={`${d.id}@${d.version}`}>
                <label className="check">
                  <input type="checkbox" checked={!excluded.has(d.id)} onChange={() => toggleDoc(d.id)} />
                  <span className="doc-id">
                    {d.id} <span className="muted">v{d.version}</span>
                  </span>
                  <span className="doc-list-title">{d.title}</span>
                  {!d.is_current && <span className="tag">superseded</span>}
                </label>
              </li>
            ))}
            {estimate && matched.length === 0 && <li className="muted">No documents match.</li>}
          </ul>
        </fieldset>

        {estimate && <EstimateLine e={estimate} />}
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !template || !name.trim() || docCount === 0}>
            {busy ? "Creating…" : `Create review (${fmtInt(estimate?.cells ?? 0)} cells)`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EstimateLine({ e }: { e: Estimate }) {
  const cost = e.cost_usd === null ? "cost unknown for this model" : `≈ $${e.cost_usd < 0.01 ? e.cost_usd.toFixed(4) : e.cost_usd.toFixed(2)}`;
  const time = e.seconds < 90 ? `${e.seconds} s` : `${Math.round(e.seconds / 60)} min`;
  return (
    <div className="hint estimate">
      <strong>
        {fmtInt(e.selected)} documents × {e.questions} questions = {fmtInt(e.cells)} cells
      </strong>
      <span>
        {" "}
        · {cost} · about {time} with {e.workers} workers
      </span>
      <div className="muted small">
        {e.calibrated
          ? "From the average tokens and latency of the cells answered so far; one cache write per document. Ignores API rate limits."
          : "Rough: no cells answered with this model yet, so default averages are used."}
      </div>
    </div>
  );
}
