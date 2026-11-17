"""Model calls: answer one cell (structured output, cached document prefix) and judge answers.

Request layout for answer_cell. Prompt caching is a prefix match over tools -> system ->
messages, so everything before the document is identical for every column:
  tools    record_answer (one schema for all answer types)
  system   SYSTEM
  user     [document text]            <- cache_control: every question on this document reuses it
           [question, answer type, instructions]
"""

import asyncio
import functools
import os
import random
import time

import anthropic

from app.models import CellResult, Column, ToolAnswer

MAX_TRIES = 4
MAX_TOKENS = 8192  # room for adaptive thinking on the strong tier; Haiku answers in a few hundred

SYSTEM = """You answer one question about one regulatory document (a stability protocol, \
specification or similar GMP record) for a pharmaceutical regulatory affairs review. Record \
your answer by calling the record_answer tool.

Rules:
- Answer only from the document. Do not use outside knowledge, and do not guess.
- Every claim in the answer must be supported by at least one citation.
- Each citation quote is one sentence or one table row, copied verbatim from the document, \
character for character: no ellipses, no paraphrasing, no joining of separate sentences.
- If the document does not contain the answer, set found to false, answer to null and \
citations to an empty list.
- reasoning is one or two sentences on how the citations support the answer."""

ANSWER_TOOL = {
    "name": "record_answer",
    "description": "Record the answer to the question about the document, with verbatim citations.",
    "strict": True,
    "input_schema": ToolAnswer.model_json_schema(),
}

TYPE_HINTS = {
    "text": "text: a concise string stating the facts",
    "list": "list: an array of strings, one item per entry ([] means the document states there are none)",
    "date": "date: a string in YYYY-MM-DD format",
    "boolean": "boolean: true or false",
}

JUDGE_TOOL = {
    "name": "record_verdict",
    "description": "Record whether the candidate answer states the same facts as the reference.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"match": {"type": "boolean"},
                       "reason": {"type": "string", "description": "One line."}},
        "required": ["match", "reason"],
        "additionalProperties": False,
    },
}

JUDGE_PROMPT = """Question: {question}

Reference answer: {reference}

Candidate answer: {candidate}

Do these two answers state the same facts? The candidate may add detail that is consistent \
with the reference, but it must include the reference's key values (conditions, numbers, \
limits, batches, durations) and must not contradict any of them. Reply with match true/false \
and a one-line reason by calling record_verdict."""

# USD per million tokens: input, output, cache read, 5-minute cache write.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),  # prompts of 100K tokens or fewer
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
}


def model_for(tier: str) -> str:
    if tier == "strong":
        return os.environ.get("ANTHROPIC_MODEL_STRONG", "claude-sonnet-5-5")
    return os.environ.get("ANTHROPIC_MODEL_FAST", "claude-haiku-5-5")


def cost_usd(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens):
    """Estimated cost in USD, or None for a model without a price entry."""
    if model not in PRICES:
        return None
    p_in, p_out, p_read, p_write = PRICES[model]
    return (input_tokens * p_in + output_tokens * p_out + cache_read_tokens * p_read
            + cache_write_tokens * p_write) / 1e6


@functools.cache
def client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(max_retries=0, timeout=90)  # create() does the retrying


def tool_choice(model: str, tool_name: str) -> dict:
    # Haiku 5.5 accepts a forced tool call. Sonnet 5.5 and Opus 5.5 reject forcing with a 400,
    # so on those the prompt asks for the call and tool_input() checks that it was made.
    if model.startswith("claude-haiku"):
        return {"type": "tool", "name": tool_name}
    return {"type": "auto"}


THROTTLED = (anthropic.RateLimitError, anthropic.OverloadedError)  # 429 and 529
TRANSIENT = (anthropic.InternalServerError, anthropic.APIConnectionError)
SPEND_CAP = "enforced_spend_limit_reached"  # a 429 that lasts until next month


def is_throttled(e: Exception) -> bool:
    """A 429 rate or ramp limit, or 529 overloaded: the API asks us to slow down and the request
    itself is fine. The monthly spend-cap 429 is not throttling: no retry can succeed."""
    return isinstance(e, THROTTLED) and e.error_code != SPEND_CAP


def backoff_s(e: Exception, attempt: int) -> float:
    """Wait before retrying: the API's retry-after header when it sends one, else exponential
    backoff (~2s, 4s, 8s). Jitter keeps throttled workers from retrying in lockstep."""
    try:
        wait = float(e.response.headers["retry-after"])
    except (AttributeError, KeyError, ValueError):
        wait = 2 ** attempt
    return wait + random.uniform(0, 1)


async def create(**kwargs):
    """messages.create, retried on throttling, 5xx and connection errors: up to MAX_TRIES tries
    in total. The spend-cap 429 is raised at once."""
    for attempt in range(1, MAX_TRIES + 1):
        try:
            return await client().messages.create(**kwargs)
        except (*THROTTLED, *TRANSIENT) as e:
            if attempt == MAX_TRIES or (isinstance(e, THROTTLED) and not is_throttled(e)):
                raise
            await asyncio.sleep(backoff_s(e, attempt))


def tool_input(msg, tool_name: str) -> dict:
    if msg.stop_reason in ("max_tokens", "refusal"):
        raise ValueError(f"model stopped with {msg.stop_reason}")
    for block in msg.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise ValueError(f"model did not call {tool_name}")


async def answer_cell(doc_text: str, column: Column) -> CellResult:
    model = model_for(column.model_tier)
    question = f"Question: {column.question}\nAnswer type: {TYPE_HINTS[column.answer_type]}"
    if column.instructions:
        question += f"\nInstructions: {column.instructions}"

    started = time.monotonic()
    msg = await create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        tools=[ANSWER_TOOL],
        tool_choice=tool_choice(model, ANSWER_TOOL["name"]),
        messages=[{"role": "user", "content": [
            {"type": "text", "text": f"<document>\n{doc_text}\n</document>",
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": question},
        ]}],
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    out = ToolAnswer.model_validate(tool_input(msg, ANSWER_TOOL["name"]))
    out.check(column.answer_type)
    usage = msg.usage
    return CellResult(
        found=out.found,
        answer=out.answer if out.found else None,
        citations=[c.quote for c in out.citations],
        reasoning=out.reasoning,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        latency_ms=latency_ms,
    )


async def judge(question: str, reference: str, candidate: str) -> tuple[bool, str]:
    """Ask the fast model whether candidate and reference state the same facts."""
    model = model_for("fast")
    msg = await create(
        model=model,
        max_tokens=1024,
        tools=[JUDGE_TOOL],
        tool_choice=tool_choice(model, JUDGE_TOOL["name"]),
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, reference=reference, candidate=candidate)}],
    )
    verdict = tool_input(msg, JUDGE_TOOL["name"])
    return bool(verdict["match"]), verdict["reason"]
