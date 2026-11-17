"""Generate the synthetic regulatory corpus from corpus/ground_truth.json.

For each document the model writes a realistic GMP document (or a health-authority
letter) from the ground-truth facts. The script then paginates it at paragraph boundaries
(`<!-- page N -->` about every 400-500 words) and checks it:
  - every evidence sentence appears exactly once (after normalize())
  - the planted cases hold (no bracketing text in STAB-011, the STAB-013
    deviation appears only on the last page)
A failing draft is regenerated up to MAX_RETRIES times, with the problems
fed back to the model; after that the script fails loudly.

Usage: python scripts/generate_corpus.py [--force] [--jobs N] [DOC_ID ...]
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.text import normalize  # noqa: E402

GT_PATH = ROOT / "corpus" / "ground_truth.json"
OUT_DIR = ROOT / "corpus" / "docs"
MAX_RETRIES = 2
PAGE_WORDS = 400  # close a page once it reaches this many words

SYSTEM = (
    "You are a senior regulatory affairs writer at {company}, a mid-size pharmaceutical "
    "company. You write controlled GMP documents (stability protocols and drug product "
    "specifications) in house style: numbered sections, precise language, tables where a real "
    "document would have them, and realistic operational detail such as batch numbers, chamber "
    "IDs, analytical method numbers, sample quantities, responsibilities and approval "
    "signatures. All people, sites and data are fictional and internally consistent."
)
LETTER_SYSTEM = (
    "You write correspondence from health authorities (FDA, EMA, MHRA, Health Canada, "
    "Swissmedic) to {company}, a mid-size pharmaceutical company, about the commitments it made "
    "for its products: formal letters with the authority's reference numbers, dates and a "
    "signature block. All people, numbers and data are fictional and internally consistent."
)

SECTIONS = {
    "stability_protocol": [
        "Purpose", "Scope", "Responsibilities", "Materials and batches", "Analytical methods",
        "Storage conditions", "Testing schedule", "Acceptance criteria", "Deviation handling",
        "Bracketing and matrixing", "Post-approval commitments",
        "References (ICH Q1A(R2), Q1B, Q1D, Q1E and others as relevant)", "Revision history",
        "Approvals",
    ],
    "ha_letter": [
        "Letterhead: the authority, the letter's date and its reference numbers",
        "Addressee and salutation", "Background: the application and how the commitment arose",
        "The commitment, its due date and its status", "Next steps and contact",
        "Closing and signature",
    ],
    "specification": [
        "Purpose", "Scope", "Product description and composition",
        "Specification table (release and shelf-life criteria, with method references)",
        "Justification of specification", "Analytical procedures", "Reference standards",
        "References (ICH Q6A, Q3B(R2) and others as relevant)", "Revision history", "Approvals",
    ],
}


def evidence(doc):
    """Yield (field, evidence sentence) for every non-null evidence in a ground-truth doc."""
    for field, v in doc["fields"].items():
        items = v if isinstance(v, list) else [v]
        for i, item in enumerate(items):
            if item["evidence"]:
                yield (f"{field}[{i}]" if isinstance(v, list) else field), item["evidence"]


def build_prompt(gt, doc):
    product = gt["products"][doc["product"]]
    facts = {f: ([d["value"] for d in v] if isinstance(v, list) else v["value"])
             for f, v in doc["fields"].items()}
    sections = list(SECTIONS[doc["doc_type"]])
    fields = doc["fields"]
    if doc["doc_type"] == "ha_letter":
        return build_letter_prompt(gt, doc, facts, sections)
    related = sorted({f"{d['id']} ({d['title']})" for d in gt["documents"]
                      if d["product"] == doc["product"] and d["id"] != doc["id"]
                      and d["doc_type"] != "ha_letter"})
    rules = [
        "Length: 1,800 to 2,600 words.",
        "The only STAB- or SPEC- documents that exist for this product are: "
        + "; ".join(related) + ". Cite only these, by these numbers; invent SOP and "
        "method numbers freely but no other STAB-/SPEC- numbers.",
        f"The revision history must end with version {doc['version']}; "
        "do not mention any later version.",
        "Surround the facts with realistic detail so they are not trivially easy to find "
        "(other conditions or limits used elsewhere, method numbers, sampling tables), but "
        "nothing you write may contradict the facts above.",
    ]

    if doc["doc_type"] == "specification":
        rules.append("Release limits may be tighter than shelf-life limits, but every limit "
                     "must be clearly labelled as release or shelf-life.")
    else:
        if not fields["deviations"]:
            rules.append("No deviations have occurred under this protocol; the deviation "
                         "section should describe the procedure and state that none are recorded.")
        if fields["bracketing_justification"]["value"] is None:
            sections.remove("Bracketing and matrixing")
            sections = [s.replace("Q1D, ", "") for s in sections]  # Q1D is the bracketing guideline
            rules.append("This protocol has no reduced design. Do not include a bracketing or "
                         "matrixing section and never use the words bracketing, bracket, "
                         "matrixing or matrix anywhere.")
        if fields["post_approval_commitment"]["value"] is None:
            sections.remove("Post-approval commitments")
            rules.append("This protocol makes no post-approval commitment. Do not include a "
                         "commitments section or describe any commitment to a health authority.")
        if doc["planted"] == "deviation_in_appendix":
            dev_id = fields["deviations"][0]["value"].split(":")[0]
            sections += ["Appendix A: Sampling plan", "Appendix B: Test method list",
                         "Appendix C: Deviation log"]
            rules.append(
                f"The deviation-handling section in the body must describe only the general "
                f"procedure. Do not mention {dev_id}, or that any deviation has occurred, "
                f"anywhere before Appendix C. Appendix C must be the final section, under 120 "
                f"words, and contain the required deviation sentence. Nothing may follow it.")

    rules.append("Markdown: one '# ' title line, then '## ' numbered section headings.")
    rules.append("Output only the document text: no YAML front matter, no page markers, "
                 "no code fences, no commentary.")
    rules_text = "\n".join(f"- {r}" for r in rules)
    required = "\n".join(f"{i}. {ev}" for i, (_, ev) in enumerate(evidence(doc), 1))
    status = "current" if doc["is_current"] else "superseded (written as originally issued)"
    return f"""Write the full text of this controlled document.

Document: {doc['id']} version {doc['version']}, effective {doc['effective_date']}, status {status}
Title: {doc['title']}
Product: {doc['product']}, {product['drug']} {product['dosage_form']}
Document type: {doc['doc_type'].replace('_', ' ')}

Facts this document conveys (JSON):
{json.dumps(facts, indent=2, ensure_ascii=False)}

REQUIRED SENTENCES. Each must appear exactly once, character for character, as ordinary prose
inside a paragraph: not in a table, heading or list item, with no bold, italics or line breaks
inside it, and not paraphrased.
{required}

Sections, in this order: {'; '.join(sections)}.

Rules:
{rules_text}"""


def build_letter_prompt(gt, doc, facts, sections):
    """A letter from a health authority about one commitment; its facts are the commitment, the
    due date, whether it is met, and its owner at the company."""
    product = gt["products"][doc["product"]]
    rules = [
        "Length: 450 to 800 words.",
        f"Write it as a letter from the health authority named in the title to {gt['company']}, "
        "addressed to the commitment's owner named in the facts.",
        "Do not cite internal document numbers (STAB-, SPEC- or HAL-); describe studies and "
        "reports in words. Invent reference, meeting and submission numbers freely.",
        "Mention other dates too (the letter's own date, when the commitment was made, a "
        "meeting or a submission), but nothing you write may contradict the facts above.",
        "Markdown: one '# ' title line (the letter's subject), then plain paragraphs; a '## ' "
        "heading only for a list of enclosures.",
        "Output only the letter text: no YAML front matter, no page markers, no code fences, "
        "no commentary.",
    ]
    rules_text = "\n".join(f"- {r}" for r in rules)
    required = "\n".join(f"{i}. {ev}" for i, (_, ev) in enumerate(evidence(doc), 1))
    return f"""Write the full text of this letter.

Letter: {doc['id']}, dated {doc['effective_date']}
Title: {doc['title']}
Product: {doc['product']}, {product['drug']} {product['dosage_form']}

Facts this letter conveys (JSON):
{json.dumps(facts, indent=2, ensure_ascii=False)}

REQUIRED SENTENCES. Each must appear exactly once, character for character, as ordinary prose
inside a paragraph: not in a table, heading or list item, with no bold, italics or line breaks
inside it, and not paraphrased.
{required}

Parts, in this order: {'; '.join(sections)}.

Rules:
{rules_text}"""


def paginate(body):
    """Split at blank lines into pages of roughly PAGE_WORDS+ words, never right after a heading."""
    pages, cur, words = [], [], 0
    for block in re.split(r"\n\s*\n", body.strip()):
        cur.append(block)
        words += len(block.split())
        if words >= PAGE_WORDS and not block.lstrip().startswith("#"):
            pages.append("\n\n".join(cur))
            cur, words = [], 0
    if cur:
        pages.append("\n\n".join(cur))
    return pages


def check(doc, body, pages):
    problems = []
    if body.lstrip().startswith(("---", "```")) or "<!--" in body:
        problems.append("output must be plain Markdown without front matter, fences or comments")
    full = normalize(body)
    for field, ev in evidence(doc):
        n = full.count(normalize(ev))
        if n != 1:
            problems.append(f"required sentence for {field} appears {n} times (must be exactly "
                            f"once, verbatim): {ev}")
    if doc["planted"] == "missing_bracketing" and re.search(r"bracket|matrix", full, re.I):
        problems.append("the words bracketing/matrixing must not appear anywhere")
    if doc["planted"] == "deviation_in_appendix":
        dev = doc["fields"]["deviations"][0]
        dev_id = dev["value"].split(":")[0]
        if (any(dev_id in p for p in pages[:-1])
                or normalize(dev["evidence"]) not in normalize(pages[-1])):
            problems.append(f"{dev_id} must be mentioned only in Appendix C, the short final "
                            f"section, and nowhere earlier")
    return problems


def write_draft(client, prompt, system):
    with client.beta.messages.stream(
        model=os.environ.get("MODEL", "claude-opus-5-5"),
        max_tokens=32000,
        thinking={"type": "adaptive"},
        output_config={"effort": os.environ.get("EFFORT", "medium")},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # re-run on a fallback model if the request is declined
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, msg.stop_reason


def render(doc, pages):
    front = "\n".join([
        "---",
        f"id: {doc['id']}",
        f"title: {json.dumps(doc['title'], ensure_ascii=False)}",
        f"product: {doc['product']}",
        f"doc_type: {doc['doc_type']}",
        f'version: "{doc["version"]}"',
        f"effective_date: {doc['effective_date']}",
        f"is_current: {str(doc['is_current']).lower()}",
        "---",
    ])
    body = "\n\n".join(f"<!-- page {i} -->\n\n{p}" for i, p in enumerate(pages, 1))
    return f"{front}\n\n{body}\n"


def generate(client, gt, doc, path):
    name = f"{doc['id']} v{doc['version']}"
    prompt = build_prompt(gt, doc)
    system = LETTER_SYSTEM if doc["doc_type"] == "ha_letter" else SYSTEM
    system = system.format(company=gt["company"])
    problems = []
    for attempt in range(1, MAX_RETRIES + 2):
        if problems:
            prompt_now = (prompt + "\n\nA previous draft failed these checks. Fix them:\n"
                          + "\n".join(f"- {p}" for p in problems))
        else:
            prompt_now = prompt
        body, stop = write_draft(client, prompt_now, system)
        pages = paginate(body)
        problems = [f"response stopped with {stop}"] if stop != "end_turn" else []
        problems += check(doc, body, pages)
        if not problems:
            path.write_text(render(doc, pages), encoding="utf-8")
            print(f"wrote {path.name}: {len(body.split())} words, {len(pages)} pages "
                  f"(attempt {attempt})", flush=True)
            return
        print(f"{name}: attempt {attempt} failed: {problems}", flush=True)
    raise RuntimeError(f"{name} still fails after {MAX_RETRIES} retries: {problems}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="*", help="only generate these document ids (default: all)")
    ap.add_argument("--force", action="store_true", help="regenerate documents that already exist")
    ap.add_argument("--jobs", type=int, default=6, help="documents generated in parallel")
    args = ap.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    todo = []
    for doc in gt["documents"]:
        path = OUT_DIR / f"{doc['id']}_v{doc['version']}.md"
        if args.ids and doc["id"] not in args.ids:
            continue
        if path.exists() and not args.force:
            print(f"skip {path.name} (exists; use --force to regenerate)")
            continue
        todo.append((doc, path))

    client = anthropic.Anthropic(max_retries=4)
    failed = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(generate, client, gt, doc, path): path.name for doc, path in todo}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:  # report every failure, then exit non-zero
                failed.append(futures[fut])
                print(f"FAILED {futures[fut]}: {e}", file=sys.stderr, flush=True)
    if failed:
        sys.exit(f"{len(failed)} document(s) failed: {', '.join(sorted(failed))}")
    print(f"done: {len(todo)} generated")


if __name__ == "__main__":
    main()
