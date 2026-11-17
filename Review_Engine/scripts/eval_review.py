"""Score a review against Ingestion/corpus/ground_truth.json.

Answer, per cell (scored on the answer even when the status is needs_review):
  - ground truth null  -> correct only if the cell is not_found
  - list columns       -> normalized sets must be equal: deviation IDs; the numbers in
                          timepoint lists ("0, 3, 6 months", "Day 7", "48 hours")
  - text columns       -> the fast model judges whether the answer states the same facts
Citation, per cell with ground-truth evidence: some verified citation is on the page of a
ground-truth evidence sentence.
Claims, over answered cells: how many claims have at least one quote found in the document,
and how many list items have a claim.
Planted cases: whether each trap built into the corpus was handled.

Usage: python scripts/eval_review.py 1
"""

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import database_url  # noqa: E402
from app.llm import judge, model_for  # noqa: E402
from app.text import locate, normalize  # noqa: E402
from app.worker import page_of  # noqa: E402

GT_PATH = ROOT.parent / "Ingestion" / "corpus" / "ground_truth.json"
JUDGE_CONCURRENCY = 8
# Their instructions were revised after trying them on these 15 protocols, so their scores are
# optimistic about unseen documents.
TUNED = {"bracketing_justification", "post_approval_commitment", "deviations"}
DEV_ID = re.compile(r"DEV-\d{4}-\d{4}")
# A timepoint list is a run of numbers with a time unit after it ("0, 3, 6 and 9 months",
# "27-month") or before it ("Days 0, 7, 14", "Day 7"). Other numbers in an answer (25°C,
# 60% RH, batch TXS-23-011, ICH Q1B Option 2) are not timepoints.
NUMBER_RUN = r"\d+(?:\.\d+)?(?:\s*(?:,|and|&)\s*\d+(?:\.\d+)?)*"
TIMEPOINTS = re.compile(rf"({NUMBER_RUN})\s*-?\s*(?:months?|weeks?|days?|hours?|h)\b"
                        rf"|\b(?:months?|weeks?|days?)\s+({NUMBER_RUN})", re.I)


def as_set(key: str, value) -> set:
    """A list answer or ground-truth value as a set that ignores wording."""
    items = [value] if isinstance(value, str) else list(value or [])
    if key == "timepoints":
        runs = TIMEPOINTS.findall(normalize(" ; ".join(items)))
        return {float(n) for run in runs for n in re.findall(r"\d+(?:\.\d+)?", "".join(run))}
    # deviations (and any other list): the deviation ID, else the normalized item text
    return {m.group(0) if (m := DEV_ID.search(i)) else normalize(i).lower() for i in items}


def as_text(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, list):
        return "; ".join(map(str, value)) if value else "(none)"
    return str(value)


def ground_truth(field) -> tuple:
    """(expected value, [evidence sentences]) for a ground-truth field."""
    if isinstance(field, list):
        return [f["value"] for f in field], [f["evidence"] for f in field if f["evidence"]]
    return field["value"], [field["evidence"]] if field["evidence"] else []


async def score(cell, expected, judge_sem) -> tuple[bool, str]:
    status, answer = cell["status"], cell["answer"]
    if expected is None:
        return status == "not_found", f"expected not_found, got {status}"
    if status not in ("done", "needs_review"):
        return False, f"expected an answer, got {status}"
    if cell["answer_type"] == "list":
        want, got = as_set(cell["key"], expected), as_set(cell["key"], answer)
        return want == got, f"missing {sorted(want - got)}, extra {sorted(got - want)}"
    async with judge_sem:
        return await judge(cell["question"], as_text(expected), as_text(answer))


async def evaluate(cells, gt_docs, texts, pages):
    judge_sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    jobs, results = [], []
    for cell in cells:
        gt_doc = gt_docs.get((cell["doc_id"], cell["version"]))
        if gt_doc is None or cell["key"] not in gt_doc["fields"]:
            continue
        expected, evidence = ground_truth(gt_doc["fields"][cell["key"]])
        doc = (cell["doc_id"], cell["version"])
        gt_pages = {page_of(hit[0], pages[doc]) for ev in evidence
                    if (hit := locate(ev, texts[doc]))}
        cited = {c["page_no"] for c in cell["citations"] if c["verified"]}
        results.append({**cell, "expected": expected, "gt_pages": gt_pages, "cited": cited,
                        "citation_ok": bool(cited & gt_pages) if gt_pages else None})
        jobs.append(score(cell, expected, judge_sem))
    for r, (ok, reason) in zip(results, await asyncio.gather(*jobs)):
        r["correct"], r["reason"] = ok, reason
    return results


def print_accuracy(results, keys):
    print(f"{'column':<26} {'answer correct':>15} {'cited on GT page':>17}")
    rows = [(k, [r for r in results if r["key"] == k]) for k in keys] + [("overall", results)]
    for key, rs in rows:
        cit = [r for r in rs if r["citation_ok"] is not None]
        print(f"{key:<26} {sum(r['correct'] for r in rs):>9}/{len(rs):<5} "
              f"{sum(r['citation_ok'] for r in cit):>11}/{len(cit):<5}"
              + ("  (instructions tuned on this set)" if key in TUNED else ""))
        if key == keys[-1]:
            print("-" * 60)


def print_claims(cells):
    """Per-claim evidence over answered cells; cells answered before per-claim citations
    (claims is null) are skipped."""
    answered = [c for c in cells if c["claims"] is not None and c["status"] in ("done", "needs_review")]
    if not answered:
        return
    claims = supported = items = items_covered = 0
    for c in answered:
        claims += len(c["claims"])
        supported += len({ct["claim_no"] for ct in c["citations"] if ct["verified"]})
        if isinstance(c["answer"], list):
            items += len(c["answer"])
            items_covered += min(len(c["answer"]), len(c["claims"]))
    print(f"claims: {supported}/{claims} have a quote found in the document; "
          f"{items_covered}/{items} list items have a claim")


def print_planted(results, gt, review_docs, last_page):
    by = {(r["doc_id"], r["version"], r["key"]): r for r in results}

    def line(ok, case, text):
        print(f"  [{ {True: 'PASS', False: 'FAIL', None: 'LATER'}[ok] }] {case:<22} {text}")

    for d in gt["documents"]:
        doc, ver, case = d["id"], d["version"], d["planted"]
        if case == "missing_bracketing":
            r = by.get((doc, ver, "bracketing_justification"))
            line(bool(r) and r["status"] == "not_found", case,
                 f"{doc} bracketing_justification -> {r['status'] if r else 'not reviewed'}"
                 " (expected not_found)")
        elif case == "deviation_in_appendix":
            r = by.get((doc, ver, "deviations"))
            dev = DEV_ID.search(d["fields"]["deviations"][0]["value"]).group(0)
            found = bool(r) and dev in as_set("deviations", r["answer"])
            on_last = bool(r) and last_page[doc, ver] in r["cited"]
            line(found and on_last, case,
                 f"{doc} deviations: {dev} {'found' if found else 'MISSED'}, cited on "
                 f"p.{','.join(map(str, sorted(r['cited']))) if r else '-'} "
                 f"(appendix is the last page, p.{last_page[doc, ver]})")
        elif case == "superseded_version" and not d["is_current"]:
            current = next(x for x in gt["documents"] if x["id"] == doc and x["is_current"])
            r = by.get((doc, current["version"], "timepoints"))
            added = (as_set("timepoints", current["fields"]["timepoints"]["value"])
                     - as_set("timepoints", d["fields"]["timepoints"]["value"]))
            excluded = (doc, ver) not in review_docs
            line(excluded and bool(r) and r["correct"], case,
                 f"{doc} v{ver} {'excluded' if excluded else 'INCLUDED'}; v{current['version']}"
                 f" timepoints {'include' if r and r['correct'] else 'MISS'} "
                 f"{', '.join(f'{n:g}' for n in sorted(added))} months (added in "
                 f"v{current['version']})")
        elif case == "contradicts_spec":
            r = by.get((doc, ver, "acceptance_criteria"))
            line(None, case,
                 f"{doc} acceptance criteria extracted {'correctly' if r and r['correct'] else 'WRONG'}"
                 "; the conflict with the product spec needs a cross-document column (not built)")

    # Other fields the ground truth marks absent (e.g. no post-approval commitment).
    planted_null = {(d["id"], d["version"], "bracketing_justification") for d in gt["documents"]
                    if d["planted"] == "missing_bracketing"}
    absent = [r for r in results if r["expected"] is None
              and (r["doc_id"], r["version"], r["key"]) not in planted_null]
    if absent:
        line(all(r["status"] == "not_found" for r in absent), "absent field",
             ", ".join(f"{r['doc_id']} {r['key']} -> {r['status']}" for r in absent))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("review", type=int)
    args = ap.parse_args()

    url = database_url()
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    gt_docs = {(d["id"], d["version"]): d for d in gt["documents"]}

    with psycopg.connect(url, row_factory=dict_row) as conn:
        cells = conn.execute(
            """SELECT c.id, c.doc_id, c.version, c.status, c.answer, c.claims,
                      col.key, col.question, col.answer_type, col.position
               FROM cells c JOIN columns col ON col.id = c.column_id
               WHERE c.review_id = %s ORDER BY col.position, c.doc_id""",
            (args.review,)).fetchall()
        if not cells:
            sys.exit(f"review {args.review} has no cells")
        citations = defaultdict(list)
        for ct in conn.execute("SELECT cell_id, claim_no, page_no, verified FROM citations"
                               " WHERE cell_id IN (SELECT id FROM cells WHERE review_id = %s)",
                               (args.review,)):
            citations[ct["cell_id"]].append(ct)
        review_docs = {(c["doc_id"], c["version"]) for c in cells}
        texts, pages = {}, {}
        for doc, ver in review_docs:
            texts[doc, ver] = conn.execute("SELECT text FROM documents WHERE id = %s"
                                           " AND version = %s", (doc, ver)).fetchone()["text"]
            pages[doc, ver] = conn.execute("SELECT page_no, char_start FROM pages WHERE doc_id = %s"
                                           " AND version = %s ORDER BY char_start",
                                           (doc, ver)).fetchall()
    for c in cells:
        c["citations"] = citations[c["id"]]

    results = asyncio.run(evaluate(cells, gt_docs, texts, pages))
    keys = list(dict.fromkeys(c["key"] for c in cells))
    print(f"review {args.review}: {len(results)} cells scored against {GT_PATH.name} "
          f"(text columns judged by {model_for('fast')})\n")
    print_accuracy(results, keys)

    counts = defaultdict(int)
    for c in cells:
        counts[c["status"]] += 1
    print("\nstatus: " + ", ".join(f"{s} {counts[s]}" for s in
                                   ("done", "not_found", "needs_review", "failed", "pending", "running")))
    print_claims(cells)

    print("\nplanted cases:")
    last_page = {d: p[-1]["page_no"] for d, p in pages.items()}
    print_planted(results, gt, review_docs, last_page)

    misses = [r for r in results if not r["correct"] or r["citation_ok"] is False]
    print(f"\nmisses ({len(misses)}):" if misses else "\nmisses: none")
    for r in misses:
        what = [] if r["correct"] else [f"answer: {r['reason']}"]
        if r["citation_ok"] is False:
            what.append(f"cited p.{sorted(r['cited'])} but evidence is on p.{sorted(r['gt_pages'])}")
        print(f"  #{r['id']} {r['doc_id']} v{r['version']} {r['key']} [{r['status']}]: "
              + "; ".join(what))
        if not r["correct"]:
            print(f"      expected: {as_text(r['expected'])[:160]}")
            print(f"      got:      {as_text(r['answer'])[:160]}")


if __name__ == "__main__":
    main()
