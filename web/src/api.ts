// Types and calls for the bulk review API (api/main.py).

export const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8010";

export type Status = "pending" | "running" | "done" | "not_found" | "needs_review" | "failed";
export type AnswerType = "text" | "list" | "date" | "boolean";
export type Tier = "fast" | "strong";
export type Answer = string | string[] | boolean | null;

export interface Question {
  id: number;
  key: string;
  question: string;
  answer_type: AnswerType;
  instructions: string | null;
  model_tier: Tier;
  position: number;
}

export type NewQuestion = Omit<Question, "id" | "position">;

export interface Template {
  name: string;
  doc_type: string;
  columns: NewQuestion[];
}

export interface ReviewInfo {
  id: number;
  name: string;
  template: string;
  doc_filter: Record<string, unknown>;
  created_at: string;
}

export interface ReviewListItem extends ReviewInfo {
  counts: Record<Status, number>;
  total: number;
  finished: number;
}

export interface Row {
  doc_id: string;
  version: string;
  title: string;
  product: string;
}

export interface CellSummary {
  id: number;
  doc_id: string;
  version: string;
  column_id: number;
  status: Status;
  short: string | null;
  page: number | null;
  pages: number[];
}

export interface Totals {
  counts: Record<Status, number>;
  total: number;
  finished: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_rate: number | null;
  cost_usd: number;
}

export interface ReviewGrid {
  review: ReviewInfo;
  columns: Question[];
  rows: Row[];
  cells: CellSummary[];
  totals: Totals;
}

export interface Citation {
  id: number;
  quote: string;
  char_start: number | null;
  char_end: number | null;
  page_no: number | null;
  verified: boolean;
  match_method: string | null;
}

export interface CellDetail {
  id: number;
  review_id: number;
  doc_id: string;
  version: string;
  column_id: number;
  key: string;
  question: string;
  answer_type: AnswerType;
  status: Status;
  answer: Answer;
  reasoning: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  latency_ms: number | null;
  attempts: number;
  last_error: string | null;
  citations: Citation[];
}

export interface Page {
  page_no: number;
  char_start: number;
  char_end: number;
}

export interface DocumentText {
  id: string;
  version: string;
  title: string;
  product: string;
  doc_type: string;
  effective_date: string | null;
  is_current: boolean;
  text: string;
  pages: Page[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_URL + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    // FastAPI validation errors come back as a list of {loc, msg}.
    const message = Array.isArray(detail)
      ? detail.map((d: { loc: string[]; msg: string }) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")
      : detail;
    throw new Error(message || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T; // No Content
  return res.json() as Promise<T>;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  templates: () => request<Template[]>("/api/templates"),
  reviews: () => request<ReviewListItem[]>("/api/reviews"),
  createReview: (name: string, template: string) =>
    request<{ id: number }>("/api/reviews", post({ name, template })),
  review: (id: number) => request<ReviewGrid>(`/api/reviews/${id}`),
  deleteReview: (id: number) => request<void>(`/api/reviews/${id}`, { method: "DELETE" }),
  addColumn: (id: number, column: NewQuestion) =>
    request<{ column_id: number; new_cells: number }>(`/api/reviews/${id}/columns`, post(column)),
  cell: (id: number) => request<CellDetail>(`/api/cells/${id}`),
  rerun: (id: number) => request<{ id: number; status: Status }>(`/api/cells/${id}/rerun`, post()),
  document: (id: string, version: string) =>
    request<DocumentText>(`/api/documents/${encodeURIComponent(id)}/${encodeURIComponent(version)}`),
  eventsUrl: (id: number) => `${API_URL}/api/reviews/${id}/events`,
  exportUrl: (id: number) => `${API_URL}/api/reviews/${id}/export.csv`,
};
