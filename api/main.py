"""HTTP API for the bulk review tool.

The API only reads and writes Postgres; it never calls the model. Creating a review or a column
inserts pending cells, and the long-lived worker (Review_Engine/scripts/run_workers.py --watch)
picks them up. Live updates are Server-Sent Events backed by polling Postgres every 500 ms.

Run from api/: uvicorn main:api --reload --host 127.0.0.1 --port 8010
"""

import asyncio
import csv
import io
import json
import re
import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Review_Engine"))
from app import reviews  # noqa: E402
from app.db import database_url  # noqa: E402
from app.models import Column  # noqa: E402

URL = database_url()
SSE_POLL_S = 0.5
SSE_PING_S = 15
# Rows are re-read for this long after the newest change sent: a worker transaction that
# started earlier (older updated_at) can commit after a later one, and would otherwise be
# skipped. Each (cell, updated_at) is still sent only once.
SSE_OVERLAP = timedelta(seconds=5)


@contextmanager
def db():
    with psycopg.connect(URL, row_factory=dict_row) as conn:  # commits when the block succeeds
        yield conn


@asynccontextmanager
async def lifespan(_: FastAPI):
    with db() as conn:
        reviews.ensure_schema(conn)  # idempotent; needs the ingestion tables
    yield


api = FastAPI(title="Bulk review API", lifespan=lifespan)
api.add_middleware(CORSMiddleware,
                   allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@api.exception_handler(reviews.ReviewError)
def review_error(_: Request, exc: reviews.ReviewError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)



class NewReview(BaseModel):
    name: str
    template: str
    doc_filter: dict | None = None


@api.get("/api/templates")
def list_templates():
    return [t.model_dump() for t in reviews.list_templates()]


@api.get("/api/reviews")
def list_reviews():
    with db() as conn:
        return reviews.list_reviews(conn)


@api.post("/api/reviews", status_code=201)
def create_review(body: NewReview):
    with db() as conn:
        return reviews.create_review(conn, body.name, body.template, body.doc_filter)


@api.get("/api/reviews/{review_id}")
def get_review(review_id: int):
    with db() as conn:
        return reviews.grid(conn, review_id)


@api.delete("/api/reviews/{review_id}", status_code=204)
def delete_review(review_id: int):
    with db() as conn:
        reviews.delete_review(conn, review_id)


@api.post("/api/reviews/{review_id}/columns", status_code=201)
def add_column(review_id: int, column: Column):
    with db() as conn:
        return reviews.add_column(conn, review_id, column)


@api.get("/api/reviews/{review_id}/events")
async def review_events(review_id: int, request: Request):
    """SSE: first every cell, then cells as they change, each batch with fresh totals."""

    async def stream():
        sent, since, quiet = {}, "-infinity", 0.0
        async with await psycopg.AsyncConnection.connect(
                URL, autocommit=True, row_factory=dict_row) as conn:
            while not await request.is_disconnected():
                rows = await (await conn.execute(
                    reviews.GRID_CELLS, {"review_id": review_id, "since": since})).fetchall()
                changed = [r for r in rows if sent.get(r["id"]) != r["updated_at"]]
                if changed:
                    sent.update((r["id"], r["updated_at"]) for r in changed)
                    since = max(r["updated_at"] for r in rows) - SSE_OVERLAP
                    totals = reviews.summarize_totals(
                        await (await conn.execute(reviews.TOTALS, (review_id,))).fetchall())
                    payload = {"cells": [reviews.cell_summary(r) for r in changed],
                               "totals": totals}
                    yield f"event: cells\ndata: {json.dumps(payload, default=str)}\n\n"
                    quiet = 0.0
                elif quiet >= SSE_PING_S:
                    yield ": ping\n\n"  # keeps idle connections open through proxies
                    quiet = 0.0
                await asyncio.sleep(SSE_POLL_S)
                quiet += SSE_POLL_S

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.get("/api/cells/{cell_id}")
def get_cell(cell_id: int):
    with db() as conn:
        return reviews.cell_detail(conn, cell_id)


@api.post("/api/cells/{cell_id}/rerun")
def rerun_cell(cell_id: int):
    with db() as conn:
        cell = reviews.cell_detail(conn, cell_id)
        if cell["status"] == "running":
            raise reviews.ReviewError(f"cell {cell_id} is running; rerun it when it finishes", 409)
        reviews.requeue(conn, cell["review_id"], cell_ids=[cell_id])
    return {"id": cell_id, "status": "pending"}


@api.get("/api/documents/{doc_id}/{version}")
def get_document(doc_id: str, version: str):
    with db() as conn:
        return reviews.document(conn, doc_id, version)


EXPORT_STATUS = {"not_found": "Not found", "failed": "Failed", "pending": "", "running": ""}


@api.get("/api/reviews/{review_id}/export.csv")
def export_csv(review_id: int):
    """One row per document, one column per question plus "<key>_citation", one entry per quote:
    "[claim] p.N: quote"."""
    with db() as conn:
        g = reviews.grid(conn, review_id)
        citations = {}
        for c in conn.execute(
                """SELECT ct.cell_id, ct.page_no, ct.quote, ct.verified,
                          c.claims ->> (ct.claim_no - 1) AS claim
                   FROM citations ct JOIN cells c ON c.id = ct.cell_id
                   WHERE c.review_id = %s ORDER BY ct.id""",
                (review_id,)):
            where = f"p.{c['page_no']}" if c["verified"] else "unverified"
            claim = f"[{c['claim']}] " if c["claim"] else ""  # null for cells answered earlier
            citations.setdefault(c["cell_id"], []).append(f"{claim}{where}: {c['quote']}")

    cells = {(c["doc_id"], c["version"], c["column_id"]): c for c in g["cells"]}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["document", "version", "title"] +
                    [h for col in g["columns"] for h in (col["key"], f"{col['key']}_citation")])
    for row in g["rows"]:
        line = [row["doc_id"], row["version"], row["title"]]
        for col in g["columns"]:
            cell = cells.get((row["doc_id"], row["version"], col["id"]))
            if cell is None:
                line += ["", ""]
                continue
            answer = EXPORT_STATUS.get(cell["status"], cell["short"] or "")
            if cell["status"] == "needs_review":
                answer = f"[needs review] {answer}"
            line += [answer, " | ".join(citations.get(cell["id"], []))]
        writer.writerow(line)

    slug = re.sub(r"[^a-z0-9]+", "-", g["review"]["name"].lower()).strip("-") or "review"
    return Response("﻿" + out.getvalue(),  # BOM: Excel then reads the file as UTF-8
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="review-{review_id}-{slug}.csv"'})
