"""
Grant requirements demo for Team 44 (agentic AI layer).

Two specialized LLM steps from our design, running on an open-source model
through Ollama on the VM's GPU:

  1. extract  - the Requirements LLM reads a funding announcement (PDF or text)
                and saves its requirements as a structured JSON list.
  2. check    - the Completeness LLM compares an application against that list
                and reports anything missing or noncompliant.

Usage:
  python3 grant_checker.py extract samples/announcement.pdf
  python3 grant_checker.py check samples/application_bad.txt
  python3 grant_checker.py check samples/application_good.txt

Set the model with --model (default: llama3.1:8b).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "llama3.1:8b"
REQUIREMENTS_FILE = Path("requirements.json")
MAX_CHARS = 40000  # keeps long announcements inside the model's context window

REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["eligibility", "required_documents", "formatting",
                                 "budget", "deadline", "other"],
                    },
                    "requirement": {"type": "string"},
                    "source_quote": {"type": "string"},
                },
                "required": ["category", "requirement", "source_quote"],
            },
        }
    },
    "required": ["requirements"],
}

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string", "enum": ["pass", "fail", "unclear"]},
                    "explanation": {"type": "string"},
                },
                "required": ["requirement", "status", "explanation"],
            },
        },
        "overall": {"type": "string", "enum": ["ready", "needs_changes"]},
    },
    "required": ["results", "overall"],
}

EXTRACT_PROMPT = """You are the Requirements LLM in a grant application assistant.
Read the funding announcement below and list every concrete requirement an applicant
must meet. Only include requirements that are actually stated in the text. Do not
invent anything. For each one, give its category, a short plain-English statement
of the requirement, and a short exact quote from the announcement it came from.

FUNDING ANNOUNCEMENT:
{text}"""

CHECK_PROMPT = """You are the Completeness LLM in a grant application assistant.
Compare the application below against each requirement in the list. For every
requirement, decide pass, fail, or unclear (unclear means the application does not
give enough information to tell). Explain each decision in one sentence. Set overall
to "ready" only if nothing failed.

REQUIREMENTS:
{requirements}

APPLICATION:
{application}"""


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        sys.exit(f"Could not read any text from {path}. If it is a scanned PDF, it needs OCR first.")
    if len(text) > MAX_CHARS:
        print(f"(Note: document is long, using the first {MAX_CHARS:,} characters)")
        text = text[:MAX_CHARS]
    return text


def ask_llm(model: str, prompt: str, schema: dict) -> dict:
    """One call to the shared model layer. Swapping to a commercial API later
    only means changing this function."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "format": schema,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": 16384},
            },
            timeout=600,
        )
    except requests.ConnectionError:
        sys.exit("Could not reach Ollama. Start it with:  ollama serve")
    if resp.status_code != 200:
        sys.exit(f"Ollama error {resp.status_code}: {resp.text[:300]}")
    content = resp.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        sys.exit(f"Model did not return valid JSON:\n{content[:500]}")


def cmd_extract(args):
    doc = read_document(Path(args.file))
    print(f"Requirements LLM ({args.model}) reading {args.file} ...")
    start = time.time()
    result = ask_llm(args.model, EXTRACT_PROMPT.format(text=doc), REQUIREMENTS_SCHEMA)
    elapsed = time.time() - start
    reqs = result.get("requirements", [])

    REQUIREMENTS_FILE.write_text(json.dumps(reqs, indent=2))
    print(f"\nFound {len(reqs)} requirements in {elapsed:.1f} seconds:\n")
    for cat in ["eligibility", "required_documents", "formatting", "budget", "deadline", "other"]:
        items = [r for r in reqs if r.get("category") == cat]
        if not items:
            continue
        print(cat.replace("_", " ").upper())
        for r in items:
            print(f"  - {r['requirement']}")
            print(f'      source: "{r["source_quote"][:120]}"')
        print()
    print(f"Saved to {REQUIREMENTS_FILE}")


def cmd_check(args):
    if not REQUIREMENTS_FILE.exists():
        sys.exit("No requirements.json yet. Run the extract step first.")
    reqs = json.loads(REQUIREMENTS_FILE.read_text())
    application = read_document(Path(args.file))
    req_lines = "\n".join(f"{i+1}. [{r['category']}] {r['requirement']}" for i, r in enumerate(reqs))

    print(f"Completeness LLM ({args.model}) checking {args.file} against {len(reqs)} requirements ...")
    start = time.time()
    result = ask_llm(args.model, CHECK_PROMPT.format(requirements=req_lines, application=application), CHECK_SCHEMA)
    elapsed = time.time() - start

    marks = {"pass": "PASS   ", "fail": "FAIL   ", "unclear": "UNCLEAR"}
    print()
    for r in result.get("results", []):
        print(f"[{marks.get(r['status'], r['status'])}] {r['requirement']}")
        if r["status"] != "pass":
            print(f"           {r['explanation']}")
    fails = sum(r["status"] == "fail" for r in result.get("results", []))
    print(f"\nOverall: {result.get('overall', '?').upper()}  ({fails} failed, {elapsed:.1f} seconds)")


def main():
    p = argparse.ArgumentParser(description="Team 44 grant requirements demo")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("extract", help="pull requirements out of an announcement")
    e.add_argument("file")
    c = sub.add_parser("check", help="check an application against saved requirements")
    c.add_argument("file")
    args = p.parse_args()
    {"extract": cmd_extract, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    main()
