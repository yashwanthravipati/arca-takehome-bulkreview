import { useState, type FormEvent } from "react";
import { api, type AnswerType, type Question, type Tier } from "../api";
import { Modal } from "./ui";

/** Documents a question is tried on before it runs everywhere. */
export const SAMPLE_DOCS = 3;

// "What is the shelf life claimed?" -> "what_is_the_shelf_life"
function keyFrom(question: string) {
  const words = question.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const key = words.slice(0, 5).join("_").slice(0, 40);
  return /^[a-z]/.test(key) ? key : `q_${key}`;
}

/**
 * Add a column, or edit one (`column` given) and answer it again. Either way the question can
 * be tried on the first few documents first; the other cells wait as "not run" until the user
 * runs them from the column header.
 */
export function ColumnModal(props: {
  reviewId: number;
  column: Question | null;
  documents: number;
  existingKeys: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { reviewId, column, documents, existingKeys, onClose, onSaved } = props;
  const editing = column !== null;
  const [question, setQuestion] = useState(column?.question ?? "");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [answerType, setAnswerType] = useState<AnswerType>(column?.answer_type ?? "text");
  const [instructions, setInstructions] = useState(column?.instructions ?? "");
  const [tier, setTier] = useState<Tier>(column?.model_tier ?? "fast");
  const canSample = documents > SAMPLE_DOCS;
  const [sampleFirst, setSampleFirst] = useState(canSample);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectiveKey = editing ? column.key : keyEdited ? key : keyFrom(question);
  const duplicate = !editing && existingKeys.includes(effectiveKey);
  const sample = canSample && sampleFirst ? SAMPLE_DOCS : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const fields = {
      question: question.trim(),
      answer_type: answerType,
      instructions: instructions.trim() || null,
      model_tier: tier,
    };
    try {
      if (editing) await api.editColumn(reviewId, column.id, fields, sample);
      else await api.addColumn(reviewId, { key: effectiveKey, ...fields }, sample);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const action = editing ? "Save and rerun" : "Add and run";
  return (
    <Modal title={editing ? `Edit column: ${column.key.replace(/_/g, " ")}` : "Add column"} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label>
          Question
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            placeholder="e.g. What container closure system is used?"
            autoFocus
            required
          />
        </label>
        {!editing && (
          <label>
            Key
            <input
              value={effectiveKey}
              onChange={(e) => {
                setKey(e.target.value);
                setKeyEdited(true);
              }}
              pattern="[a-z][a-z0-9_]*"
              title="Lowercase letters, digits and underscores, starting with a letter"
              required
            />
            {duplicate && <span className="field-error">This review already has a column with this key.</span>}
          </label>
        )}
        <div className="form-row">
          <label>
            Answer type
            <select value={answerType} onChange={(e) => setAnswerType(e.target.value as AnswerType)}>
              <option value="text">Text</option>
              <option value="list">List</option>
              <option value="date">Date</option>
              <option value="boolean">Yes / no</option>
            </select>
          </label>
          <label>
            Model tier
            <select value={tier} onChange={(e) => setTier(e.target.value as Tier)}>
              <option value="fast">Fast (Haiku)</option>
              <option value="strong">Strong (Sonnet)</option>
            </select>
          </label>
        </div>
        <label>
          <span>
            Instructions <span className="muted">(optional)</span>
          </span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={3}
            placeholder="What counts as an answer, what to leave out, when to answer not found."
          />
        </label>
        {canSample && (
          <fieldset className="run-on">
            <legend>Run on</legend>
            <label className="radio">
              <input type="radio" checked={sampleFirst} onChange={() => setSampleFirst(true)} />
              The first {SAMPLE_DOCS} documents, to check the question. The other {documents - SAMPLE_DOCS} wait
              until you run them from the column header.
            </label>
            <label className="radio">
              <input type="radio" checked={!sampleFirst} onChange={() => setSampleFirst(false)} />
              All {documents} documents
            </label>
          </fieldset>
        )}
        <div className="hint">
          {editing
            ? `Saving clears this column's ${documents} answers and their citations, then answers it again. Other columns are kept.`
            : "Only this column's cells are queued; existing answers are kept."}
        </div>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || duplicate || !question.trim()}>
            {busy ? "Saving…" : sample ? `${action} on ${SAMPLE_DOCS}` : action}
          </button>
        </div>
      </form>
    </Modal>
  );
}
