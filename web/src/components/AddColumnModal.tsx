import { useState, type FormEvent } from "react";
import { api, type AnswerType, type Tier } from "../api";
import { Modal } from "./ui";

// "What is the shelf life claimed?" -> "what_is_the_shelf_life"
function keyFrom(question: string) {
  const words = question.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const key = words.slice(0, 5).join("_").slice(0, 40);
  return /^[a-z]/.test(key) ? key : `q_${key}`;
}

export function AddColumnModal(props: {
  reviewId: number;
  existingKeys: string[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const { reviewId, existingKeys, onClose, onAdded } = props;
  const [question, setQuestion] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [answerType, setAnswerType] = useState<AnswerType>("text");
  const [instructions, setInstructions] = useState("");
  const [tier, setTier] = useState<Tier>("fast");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectiveKey = keyEdited ? key : keyFrom(question);
  const duplicate = existingKeys.includes(effectiveKey);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.addColumn(reviewId, {
        key: effectiveKey,
        question: question.trim(),
        answer_type: answerType,
        instructions: instructions.trim() || null,
        model_tier: tier,
      });
      onAdded();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal title="Add column" onClose={onClose}>
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
          Instructions <span className="muted">(optional)</span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={3}
            placeholder="What counts as an answer, what to leave out, when to answer not found."
          />
        </label>
        <div className="hint">Only this column's cells are queued; existing answers are kept.</div>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || duplicate || !question.trim()}>
            {busy ? "Adding…" : "Add and run"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
