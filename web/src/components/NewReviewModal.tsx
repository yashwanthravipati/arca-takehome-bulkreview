import { useEffect, useState, type FormEvent } from "react";
import { api, type Template } from "../api";
import { Modal } from "./ui";

export function NewReviewModal({ onClose }: { onClose: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [template, setTemplate] = useState("");
  const [name, setName] = useState(`Stability review ${new Date().toISOString().slice(0, 10)}`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.templates().then((t) => {
      setTemplates(t);
      setTemplate((current) => current || t[0]?.name || "");
    }, (e: Error) => setError(e.message));
  }, []);

  const chosen = templates.find((t) => t.name === template);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createReview(name, template);
      window.location.hash = `#/reviews/${id}`;
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal title="New review" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
        </label>
        <label>
          Template
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            {templates.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        {chosen && (
          <div className="hint">
            Runs {chosen.columns.length} questions over every current{" "}
            <strong>{chosen.doc_type.replace(/_/g, " ")}</strong>:{" "}
            {chosen.columns.map((c) => c.key.replace(/_/g, " ")).join(", ")}.
          </div>
        )}
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !template || !name.trim()}>
            {busy ? "Creating…" : "Create review"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
