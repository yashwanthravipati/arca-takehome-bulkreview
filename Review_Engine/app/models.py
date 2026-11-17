"""Pydantic models: template columns, the record_answer tool's input, and a cell's result."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnswerType = Literal["text", "list", "date", "boolean"]
Answer = str | list[str] | bool | None


class Column(BaseModel):
    """One question of a review: a column of the grid."""
    key: str
    question: str
    answer_type: AnswerType
    instructions: str | None = None
    model_tier: Literal["fast", "strong"] = "fast"


class Template(BaseModel):
    name: str
    doc_type: str  # create_review.py reviews current documents of this type by default
    columns: list[Column]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(description="One fact the answer states, in a few words: a list item, or "
                                   "one part of a text answer (for example one storage condition).")
    quotes: list[str] = Field(description="Verbatim quotes supporting this claim. Each is one "
                                          "sentence or table row copied character for character "
                                          "from the document: no ellipses, no paraphrasing, no "
                                          "joining of separate sentences.")


# Input schema of the record_answer tool: the model's structured answer for one cell. One schema
# serves every answer type so that all columns send identical tools and the cached document
# prefix is shared across columns; check() then enforces the column's own type. (No docstring:
# pydantic would send it to the model as the schema description.)
class ToolAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    found: bool = Field(description="false if the document does not contain the answer.")
    answer: Answer = Field(description="In the answer type the question asks for; "
                                       "null when found is false.")
    claims: list[Claim] = Field(description="The answer split into its claims, each with its own "
                                            "quotes: one claim per list item in the same order, "
                                            "or one per fact of a text answer. An empty list "
                                            "answer gets one claim saying there are none. Empty "
                                            "when found is false.")
    reasoning: str = Field(description="One or two sentences on how the quotes support the answer.")

    def check(self, answer_type: AnswerType) -> None:
        """Raise ValueError if a found answer is not of the column's answer type, or has no
        claims (an answer with no evidence at all is malformed output; the worker retries)."""
        if not self.found:
            return
        if not self.claims:
            raise ValueError("found answer has no claims")
        a = self.answer
        ok = {
            "text": isinstance(a, str) and bool(a.strip()),
            "list": isinstance(a, list),
            "boolean": isinstance(a, bool),
            "date": isinstance(a, str) and _is_iso_date(a),
        }[answer_type]
        if not ok:
            raise ValueError(f"answer {a!r} is not a valid {answer_type}")


def _is_iso_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


class CellResult(BaseModel):
    found: bool
    answer: Answer
    claims: list[Claim]  # quotes as the model wrote them; the worker verifies them
    reasoning: str
    model: str
    input_tokens: int  # uncached input only
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
