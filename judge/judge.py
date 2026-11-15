"""The engine that runs the agents in the agents/ folder (the Judge and one file per LLM).

    python judge.py chat                          talk to the Judge in the terminal
    python judge.py pull                          download every local model the agents use
    python judge.py check-db                      show the database's collections
    python judge.py add-document FILE REQUEST-ID  store a grant (.docx/.pdf/.txt/.md) for a request
    python judge.py setup-demo                    load the made-up SAMPLE grants (our database only)

How it works: the Judge (an LLM) reads the message and either answers or hands it to a specialist.
For a named request, the code fetches all its records and computes the exact facts (missing items,
budget totals and limits, page limits); the specialist (its own LLM) explains them, and its answer
goes straight to the user. The LLMs never write database code and can only read: they pick an
operation and give JSON arguments, and anything that could change data is refused.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
# The agents folder: settings.json, judge.json, and llms/<name>.json (one file per LLM).
AGENTS_DIR = next((p for p in (HERE / "agents", HERE.parent / "agents") if p.is_dir()), HERE.parent / "agents")
MAX_STEPS = 8            # tool calls an agent may make before it must answer
MIN_DOCS = 10            # fewest documents a query returns (see check_query)
MAX_DOCS = 20            # most documents one query may return
MAX_RESULT_CHARS = 6000  # longest query result shown to an LLM


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path.relative_to(AGENTS_DIR.parent)} has a typo on line {e.lineno}: {e.msg} "
                         "(often a missing comma or quote).")


def load_agents():
    """The agents folder, read fresh each time so edits apply to the next message.
    Each file in agents/llms/ is one specialist LLM, named after the file. Files starting with
    _ (like _TEMPLATE.json) are ignored, so adding or removing an LLM is adding or deleting a file."""
    return {"settings": _read_json(AGENTS_DIR / "settings.json"),
            "judge": _read_json(AGENTS_DIR / "judge.json"),
            "specialists": {p.stem: _read_json(p) for p in sorted((AGENTS_DIR / "llms").glob("*.json"))
                            if not p.name.startswith("_")}}


def setting(name):
    """A value from agents/settings.json; an environment variable of the same name in
    capitals (e.g. MONGO_URL) overrides it."""
    return os.environ.get(name.upper()) or load_agents()["settings"][name]


# The team's own databases. The Judge may READ them, but never writes to them: its writes
# (team members, uploaded drafts, demo data) go only to settings.mongo_db (default "grants").
PROTECTED_DBS = {"grant_rag", "affiliation", "admin", "local", "config"}


def write_db():
    """The database the Judge may write to, or an error if it's set to a protected one."""
    name = setting("mongo_db")
    library = (os.environ.get("LIBRARY_DB") or load_agents()["settings"].get("library_db") or "").lower()
    if name.lower() in PROTECTED_DBS or name.lower() == library:
        raise PermissionError(f"Refusing to write to the team database {name!r}. "
                              f"Set mongo_db (or MONGO_DB) to a database of our own, like 'grants'.")
    return name


class LLMError(RuntimeError):
    pass


# ============================================================================ LLM (Ollama)

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["tool", "answer"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "answer": {"type": "string"},
    },
    "required": ["action", "tool", "args", "answer"],
}


def claude_call(model, system, prompt, schema=None):
    """One call to Claude through the `claude` program (Claude Code), using the signed-in Claude
    account (`claude auth login`), so no API key is needed. Stateless: the prompt holds everything."""
    exe = shutil.which("claude") or str(Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe")
    cmd = [exe, "-p", "--model", model, "--output-format", "json", "--system-prompt", system,
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    # Hide any API key, so the claude program uses the Claude login instead.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              timeout=600, env=env)
    except FileNotFoundError:
        raise LLMError("The claude program isn't installed. Install Claude Code, then run: claude auth login")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"Claude gave no answer: {(proc.stderr or proc.stdout).strip()[:300]}")
    if data.get("is_error"):
        raise LLMError(f"Claude error: {data.get('result')}  (signed in? run: claude auth login)")
    return data.get("structured_output") if schema else (data.get("result") or "").strip()


def transcript(messages):
    return "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)


def chat_json(model, system, messages):
    """One LLM call; the reply is JSON matching STEP_SCHEMA, so small models use tools reliably.
    Models whose name starts with "claude" go to Claude; all others to Ollama."""
    if model.startswith("claude"):
        step = claude_call(model, system, transcript(messages), STEP_SCHEMA)
        if not isinstance(step, dict):
            raise LLMError(f"Model '{model}' didn't return a valid step.")
        return step
    try:
        response = httpx.post(f"{setting('ollama_url')}/api/chat", timeout=900, json={
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": STEP_SCHEMA,
            "stream": False,
            # num_ctx = working memory in tokens. Ollama's default (~4k) is too small for the
            # instructions plus a request's records, and the model silently loses part of them.
            "options": {"temperature": 0, "num_ctx": 8192},
        })
    except httpx.HTTPError as e:
        raise LLMError(f"The LLMs aren't reachable ({e}). Is Ollama running?")
    if response.status_code == 404:
        raise LLMError(f"Model '{model}' isn't downloaded. Run: python judge.py pull")
    response.raise_for_status()
    try:
        return json.loads(response.json()["message"]["content"])
    except (KeyError, json.JSONDecodeError):
        raise LLMError(f"Model '{model}' didn't return valid JSON.")


def chat_text(model, system, user_text):
    """One LLM call with a plain-text reply (no JSON): used for final answers, which small models
    write much better without a format to fill in."""
    if model.startswith("claude"):
        return claude_call(model, system, user_text)
    try:
        response = httpx.post(f"{setting('ollama_url')}/api/chat", timeout=900, json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192},
        })
    except httpx.HTTPError as e:
        raise LLMError(f"The LLMs aren't reachable ({e}). Is Ollama running?")
    if response.status_code == 404:
        raise LLMError(f"Model '{model}' isn't downloaded. Run: python judge.py pull")
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


# ============================================================================ database (mongosh)

QUERY_OPS = ("collections", "layout", "request", "sample", "find", "count", "aggregate",
             "library_list", "library_read", "library_search")
FORBIDDEN = {"$out", "$merge", "$function", "$accumulator", "$where"}  # write data or run code
MONGOSH = (os.environ.get("MONGOSH") or shutil.which("mongosh")
           or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "mongosh" / "mongosh.exe"))

# The only script mongosh ever runs. Arguments arrive as JSON in an environment variable.
MONGOSH_SCRIPT = r"""
const a = EJSON.parse(process.env.JUDGE_ARGS);
const d = db.getSiblingDB(process.env.JUDGE_DB);
let out;
if (a.op === "collections") out = d.getCollectionNames();
else if (a.op === "layout") out = Object.fromEntries(d.getCollectionNames().map(
    c => [c, d.getCollection(c).findOne({}, {_id: 0}) || {}]));
else if (a.op === "request") {
  // Everything about one request: its records in every collection (linked by request_id),
  // the funding opportunity it applies to, and the general policies.
  out = {};
  for (const c of d.getCollectionNames()) {
    const docs = d.getCollection(c).find({request_id: {$regex: "^" + a.id + "$", $options: "i"}}, {_id: 0}).toArray();
    if (docs.length) out[c] = docs;
  }
  const r = (out.requests || [])[0];
  if (r && r.opportunity_id) out.opportunity = d.getCollection("opportunities").findOne({opportunity_id: r.opportunity_id}, {_id: 0});
  if (r) out.policies = d.getCollection("policies").find({}, {_id: 0}).limit(20).toArray();
}
else if (a.op === "sample") out = d.getCollection(a.collection).find({}).limit(a.limit).toArray();
else if (a.op === "find") out = d.getCollection(a.collection).find(a.filter, a.projection).limit(a.limit).toArray();
else if (a.op === "count") out = d.getCollection(a.collection).countDocuments(a.filter);
else if (a.op === "aggregate") out = d.getCollection(a.collection).aggregate(a.pipeline.concat([{$limit: a.limit}])).toArray();
// The team's library of past grants (chunks of text with a title, PI and page number): read only.
else if (a.op === "library_list") out = d.getCollection(a.collection).aggregate([
    {$group: {_id: "$doc_link", title: {$first: "$document_title"}, pi: {$first: "$principal_investigator"},
              organization: {$first: "$source_organization"}, notice: {$first: "$sample_notice"}, chunks: {$sum: 1}}},
    {$sort: {title: 1}}, {$limit: 200}]).toArray();
else if (a.op === "library_read") out = d.getCollection(a.collection).find({$or: [
    {document_title: {$regex: a.q, $options: "i"}}, {principal_investigator: {$regex: a.q, $options: "i"}},
    {doc_link: {$regex: a.q, $options: "i"}}]},
    {_id: 0, text: 1, page_number: 1, chunk_id: 1, document_title: 1, principal_investigator: 1, doc_link: 1})
  .sort({doc_link: 1, chunk_id: 1}).limit(400).toArray();
else if (a.op === "library_search") out = d.getCollection(a.collection).find({text: {$regex: a.q, $options: "i"}},
    {_id: 0, text: 1, page_number: 1, document_title: 1, principal_investigator: 1}).limit(12).toArray();
print(EJSON.stringify(out, undefined, 0, {relaxed: true}));
"""


class QueryError(ValueError):
    pass


def _forbidden_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in FORBIDDEN:
                yield k
            yield from _forbidden_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _forbidden_keys(v)


def _loose_text(filter_):
    """Make plain text matches ignore capitals: {"section": "budget justification"} also finds
    "Budget Justification". Operators the model wrote itself ($regex, $in, ...) are left alone."""
    if not isinstance(filter_, dict):
        return filter_
    loose = {}
    for key, value in filter_.items():
        if key.startswith("$"):
            loose[key] = [_loose_text(v) for v in value] if isinstance(value, list) else value
        elif isinstance(value, str):
            loose[key] = {"$regex": f"^{re.escape(value)}$", "$options": "i"}
        else:
            loose[key] = value
    return loose


def check_query(args):
    """Validate and complete query arguments, or raise QueryError saying what is wrong."""
    op = args.get("op")
    if op not in QUERY_OPS:
        raise QueryError(f"op must be one of {QUERY_OPS}")
    clean = {"op": op}
    if op == "request":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", str(args.get("id", ""))):
            raise QueryError("id must be a plain request id")
        return {"op": op, "id": args["id"]}
    if op not in ("collections", "layout"):
        name = str(args.get("collection", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name):
            raise QueryError("collection must be a plain collection name")
        clean["collection"] = name
        # Small models often ask for 1 record and miss the rest (a request has a budget per year,
        # a document has several sections), so every lookup returns at least MIN_DOCS.
        clean["limit"] = max(MIN_DOCS, min(int(args.get("limit") or MIN_DOCS), MAX_DOCS))
    if op in ("library_read", "library_search"):
        text = str(args.get("q", "")).strip()
        if not 2 <= len(text) <= 200:
            raise QueryError("q must be 2 to 200 characters of plain text")
        clean["q"] = re.escape(text)   # matched as plain text, never as a pattern
    if op in ("find", "count"):
        clean["filter"] = _loose_text(args.get("filter") or {})
    if op == "find":
        clean["projection"] = args.get("projection") or {}
    if op == "aggregate":
        if not isinstance(args.get("pipeline"), list):
            raise QueryError("pipeline must be a list of stages")
        clean["pipeline"] = args["pipeline"]
    bad = sorted(set(_forbidden_keys(clean)))
    if bad:
        raise QueryError(f"not allowed (read-only access): {', '.join(bad)}")
    return clean


def run_query(args, max_chars=MAX_RESULT_CHARS, db=None):
    """Run one read-only query with mongosh; returns the result as JSON text.
    db: which database to read (default: our own, settings.mongo_db)."""
    clean = check_query(args)
    env = {**os.environ, "JUDGE_ARGS": json.dumps(clean), "JUDGE_DB": db or setting("mongo_db")}
    try:
        proc = subprocess.run([MONGOSH, setting("mongo_url"), "--quiet", "--norc", "--eval", MONGOSH_SCRIPT],
                              capture_output=True, text=True, timeout=30, env=env)
    except FileNotFoundError:
        raise QueryError("mongosh is not installed")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        if re.search(r"ECONNREFUSED|MongoNetworkError|MongoServerSelectionError|getaddrinfo", detail):
            raise QueryError("DATABASE UNAVAILABLE: the database can't be reached right now. Do not retry: "
                             "answer that the records can't be checked until the database is connected.")
        raise QueryError(detail[-500:] or "mongosh failed")
    out = proc.stdout.strip()
    if len(out) > max_chars:
        out = out[:max_chars] + " ... (shortened: ask for fewer documents or a projection)"
    return out


def request_records(*texts):
    """If the texts name a request (e.g. "SAMPLE-001", or the one on screen), return all of its
    records as JSON text, so a specialist can answer by reading instead of searching. Else None."""
    for text in texts:
        for request_id in re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", text or ""):
            try:
                records = run_query({"op": "request", "id": request_id}, max_chars=14000)
            except QueryError:
                return None
            if '"requests"' in records:
                return request_id, records
    return None


DATABASE_TOOLS = {
    "collections": "list the collections. args: {}",
    "sample": 'show a few documents to learn the fields. args: {"collection": str, "limit": int}',
    "find": 'find documents. args: {"collection": str, "filter": {...}, "projection": {...}, "limit": int}',
    "count": 'count documents. args: {"collection": str, "filter": {...}}',
    "aggregate": 'run an aggregation. args: {"collection": str, "pipeline": [...], "limit": int}',
}


# ============================================================================ library of past grants
# The team's own database of past grant applications, split into chunks of text (made by the
# team's uploader / retrieval code). Read only: settings.library_db and settings.library_collection.

def _library():
    s = load_agents()["settings"]
    db = os.environ.get("LIBRARY_DB") or s.get("library_db")
    if not db:
        raise QueryError("No library of past grants is set up (library_db in agents/settings.json).")
    return db, os.environ.get("LIBRARY_COLLECTION") or s.get("library_collection") or "past_submissions"


def list_past_grants():
    db, collection = _library()
    return run_query({"op": "library_list", "collection": collection}, db=db)


def read_past_grant(which):
    """The full text of one past grant, found by words from its title, its PI's name or its file name."""
    db, collection = _library()
    chunks = json.loads(run_query({"op": "library_read", "collection": collection, "q": which},
                                  max_chars=10**7, db=db))
    if not chunks:
        return f"No past grant matches {which!r}. Use list_past_grants to see the titles."
    by_doc = {}
    for c in chunks:
        by_doc.setdefault(c.get("doc_link"), []).append(c)
    if len(by_doc) > 1:
        titles = "; ".join(f"{v[0].get('document_title')} (PI {v[0].get('principal_investigator')})"
                           for v in by_doc.values())
        return f"{len(by_doc)} past grants match {which!r}: {titles}. Ask with more of the title."
    first = chunks[0]
    text = "\n".join(f"[page {c.get('page_number', '?')}] {c.get('text', '')}" for c in chunks)
    if len(text) > 30000:
        text = text[:30000] + "\n... (the rest is cut off)"
    return (f"TITLE: {first.get('document_title')}\nPI: {first.get('principal_investigator')}\n"
            f"FILE: {first.get('doc_link')}\n\n{text}")


def search_past_grants(words):
    db, collection = _library()
    return run_query({"op": "library_search", "collection": collection, "q": words}, max_chars=12000, db=db)


LIBRARY_TOOLS = {
    "list_past_grants": ("list every past grant in the team's library (title, PI, organization). args: {}",
                         lambda args: list_past_grants()),
    "read_past_grant": ('read the whole text of one past grant. args: {"which": "words from its title or PI name"}',
                        lambda args: read_past_grant(str(args.get("which", "")))),
    "search_past_grants": ('find passages in past grants that contain these exact words. '
                           'args: {"words": "a short phrase, e.g. specific aims"}',
                           lambda args: search_past_grants(str(args.get("words", "")))),
}


# ============================================================================ agents

class Final(str):
    """A tool result that is already the finished answer: the agent returns it to the user as is."""


class Agent:
    """An LLM that can use tools: it picks a tool, sees the result, and repeats until it answers.
    tools = {name: (description, function(args) -> str)}. Remembers its conversation.
    A tool that returns Final(text) ends the turn with that text as the answer."""

    def __init__(self, name, model, instructions, tools, on_event=print):
        self.name, self.model, self.tools, self.on_event = name, model, tools, on_event
        tool_list = "\n".join(f"- {n}: {desc}" for n, (desc, _) in tools.items()) or "(none)"
        self.system = ("\n".join(instructions) + "\n\nTOOLS:\n" + tool_list + "\n\nReply with JSON only. "
                       'To use a tool: {"action": "tool", "tool": "<name>", "args": {...}, "answer": ""}. '
                       'To reply: {"action": "answer", "tool": "", "args": {}, "answer": "<your reply>"}. '
                       'Never write that you will use a tool: use it, with action "tool". Use action "answer" '
                       "only for your finished reply, after you have every result you need. Never put "
                       "placeholders like [details here] in an answer: write the actual details.")
        self.messages = []

    def ask(self, text):
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            step = chat_json(self.model, self.system, self.messages)
            self.messages.append({"role": "assistant", "content": json.dumps(step)})
            if step.get("action") == "answer":
                return step.get("answer", "")
            name, args = step.get("tool", ""), step.get("args") or {}
            self.on_event(f"{self.name} uses {name} {json.dumps(args)}")
            try:
                result = self.tools[name][1](args) if name in self.tools else f"ERROR: no tool named {name!r}"
            except Exception as e:  # errors go back to the model so it can fix its call
                result = f"ERROR: {e}"
            self.messages.append({"role": "user", "content": f"TOOL RESULT ({name}):\n{result}"})
            if isinstance(result, Final):
                self.messages.append({"role": "assistant", "content": json.dumps(
                    {"action": "answer", "tool": "", "args": {}, "answer": str(result)})})
                return str(result)
        return "Sorry, I couldn't finish that within the step limit. Please rephrase or narrow it down."


def ask_specialist(name, question, on_event=print, user_message=""):
    """A fresh specialist for each question, built from its file in agents/llms/. Its answer goes
    straight to the user (Final), so it isn't reworded and garbled by another model.

    When a request is named (in the question or on screen), the code fetches all of its records and
    computes the facts a small model gets wrong (missing items, totals, limits), and the specialist
    writes one plain-text answer from them. Otherwise it searches the database with tools."""
    agents = load_agents()
    spec = agents["specialists"][name]
    style = "\n" + "\n".join(agents["settings"].get("reply_style", []))   # applies to every answer
    found = request_records(question, user_message) if spec.get("database") else None
    if found and spec.get("documents_only"):
        # Summaries and rewrites: give only the grant's own text, so the answer is about what the
        # grant says, not about the request's status.
        request_id, records = found
        on_event(f"{name} specialist reads the documents for {request_id} ({spec['model']})")
        text = document_text(records)
        if not text:
            return Final(f"There's no document text on file for {request_id} yet. Add the grant file with: "
                         f"python judge\\judge.py add-document <file> {request_id}")
        system = ("\n".join(spec["instructions"]) + "\nAnswer in plain text. Never use placeholders, and "
                  "never add anything that isn't in the document." + style)
        return Final(chat_text(spec["model"], system, f"REQUEST: {question}\n\nTHE GRANT DOCUMENT FOR "
                                                      f"{request_id}:\n{text}"))
    if found:
        request_id, records = found
        on_event(f"{name} specialist reads all records for {request_id} ({spec['model']})")
        facts = request_facts(records)
        prompt = (f"QUESTION: {question}\n\nFACTS FOR {request_id} (computed exactly by the system; trust "
                  f"these over your own arithmetic):\n{facts}\n\nALL RECORDS FOR {request_id}:\n{records}")
        system = ("\n".join(spec["instructions"]) + "\nAnswer the question directly in plain text, using the "
                  "facts and records above. Be specific: name the items, amounts and dates. Never use "
                  "placeholders, and never mention facts that aren't in the records." + style)
        return Final(chat_text(spec["model"], system, prompt))
    if spec.get("needs_request"):
        return ("No request is selected or named, so ask the user which request they mean "
                f"({', '.join(request_ids()) or 'e.g. SAMPLE-001'}). Do not answer about any request yet.")
    tools = {}
    instructions = list(spec["instructions"]) + agents["settings"].get("reply_style", [])
    if spec.get("database"):
        tools = {op: (desc, lambda args, op=op: run_query({**args, "op": op}))
                 for op, desc in DATABASE_TOOLS.items()}
        instructions.append(database_layout())
    if spec.get("library"):
        tools.update(LIBRARY_TOOLS)
    tools.update(_named_tools(spec))
    return Final(Agent(f"{name} specialist", spec["model"], instructions, tools, on_event).ask(question))


def document_text(records_text):
    """A request's documents as readable text: each section's heading and text, in order."""
    try:
        docs = json.loads(records_text).get("documents", [])
    except ValueError:
        return ""
    docs = sorted((d for d in docs if "formatting" not in d), key=lambda d: (d.get("document", ""), d.get("order", 0)))
    return "\n\n".join(f"## {d.get('section')}\n{d.get('text', '')}" for d in docs)


def request_ids():
    try:
        return [r["request_id"] for r in json.loads(run_query({"op": "find", "collection": "requests",
                                                               "projection": {"request_id": 1, "_id": 0}}))]
    except (QueryError, ValueError, KeyError, TypeError):
        return []


# ============================================================================ facts (computed in code)
# Small local models get arithmetic and checklists wrong, so the exact checks happen here and the
# model only explains them. These match the demo data's fields; adjust them to the team's real data.

def request_facts(records_text):
    try:
        r = json.loads(records_text)
    except ValueError:
        return "(records too long to check automatically)"
    facts = []
    request = (r.get("requests") or [{}])[0]
    items = request.get("items", [])
    facts.append(f"Status: {request.get('status', 'unknown')}")
    facts.append("Items done: " + (", ".join(i["name"] for i in items if i.get("done")) or "none"))
    facts.append("Items still missing: " + (", ".join(i["name"] for i in items if not i.get("done")) or "none"))
    opportunity = r.get("opportunity") or {}
    if opportunity:
        listed = {i["name"].lower() for i in items}
        extra = [x for x in opportunity.get("required_items", []) if x.lower().split(" (")[0] not in listed]
        facts.append(f"Funding opportunity: {opportunity.get('opportunity_id')} ({opportunity.get('mechanism', '')})")
        if extra:
            facts.append("Required by the opportunity but not on the request's checklist: " + ", ".join(extra))

    budgets = sorted(r.get("budgets", []), key=lambda b: b.get("year", 0))
    if budgets:
        categories = ("personnel", "equipment", "supplies", "travel")
        totals = []
        for b in budgets:
            total = sum(b.get(c, 0) for c in categories)
            totals.append(total)
            parts = " + ".join(f"{c} ${b.get(c, 0):,}" for c in categories)
            facts.append(f"Budget year {b.get('year')}: {parts} = ${total:,} direct costs")
        facts.append(f"Budget total, all years: ${sum(totals):,} direct costs")
        rules = opportunity.get("budget_rules", "")
        if rules:
            facts.append(f"Budget rules: {rules}")
            per_year = re.search(r"no more than \$([\d,]+) in any one year", rules)
            overall = re.search(r"(?:may not exceed|up to) \$([\d,]+)", rules)
            if per_year:
                cap = int(per_year.group(1).replace(",", ""))
                for b, t in zip(budgets, totals):
                    facts.append(f"Year {b.get('year')}: ${t:,} is {'OVER' if t > cap else 'within'} the ${cap:,} yearly cap")
            if overall:
                cap = int(overall.group(1).replace(",", ""))
                facts.append(f"Total ${sum(totals):,} is {'OVER' if sum(totals) > cap else 'within'} the ${cap:,} limit")
        justification = " ".join(d.get("text", "") for d in r.get("documents", [])
                                 if "justification" in d.get("section", "").lower()).lower()
        if justification:
            funded = [c for c in categories if any(b.get(c, 0) for b in budgets)]
            missing = [c for c in funded if c not in justification]
            facts.append("Budget categories NOT explained in the budget justification: " + (", ".join(missing) or "none"))

    limits = {k.lower(): v for k, v in opportunity.get("page_limits", {}).items()}
    for d in r.get("documents", []):
        limit = limits.get(d.get("section", "").lower())
        if limit and d.get("pages", 0) > limit:
            facts.append(f"{d['section']} is {d['pages']} pages, over the {limit}-page limit")
        for placeholder in ("TBD", "TODO", "[insert"):
            if placeholder in d.get("text", ""):
                facts.append(f"{d.get('section')} still contains the placeholder '{placeholder}'")
    rules = opportunity.get("formatting_rules", {})
    for d in r.get("documents", []):
        fmt = d.get("formatting")
        if not fmt:
            continue
        facts.append(f"Formatting of {d.get('document')}: fonts {', '.join(fmt['fonts'])}; font sizes "
                     f"{', '.join(f'{s:g}' for s in fmt['font_sizes'])} pt; margins (inches) {fmt['margins_in']}")
        if not rules:
            facts.append("No formatting rules are on file for this opportunity, so formatting can't be checked.")
            continue
        problems = []
        allowed = [f.lower() for f in rules.get("allowed_fonts", [])]
        bad_fonts = [f for f in fmt["fonts"] if allowed and f.lower() not in allowed]
        if bad_fonts:
            problems.append(f"font {', '.join(bad_fonts)} is not allowed (allowed: {', '.join(rules['allowed_fonts'])})")
        small = [s for s in fmt["font_sizes"] if s < rules.get("min_font_size", 0)]
        if small:
            problems.append(f"font size {', '.join(f'{s:g}' for s in small)} pt is below the "
                            f"{rules['min_font_size']} pt minimum")
        narrow = {side: v for side, v in fmt["margins_in"].items() if v < rules.get("min_margin_in", 0)}
        if narrow:
            problems.append(f"margins {narrow} are below the {rules['min_margin_in']} inch minimum")
        facts.append("Formatting problems: " + ("; ".join(problems) if problems else
                     "none, formatting meets the rules (" + rules.get("source", "opportunity rules") + ")"))
    for c in r.get("compliance", []):
        facts.append(f"{c.get('document')} ({c.get('kind')}): version {c.get('version')}, waiting on "
                     f"{c.get('whose_turn')}. Last note: {c.get('last_note')}")
    upcoming = sorted(r.get("deadlines", []), key=lambda x: x.get("due", ""))
    for x in upcoming:
        facts.append(f"Deadline {x.get('due')}: {x.get('what')} ({x.get('type')})")
    return "\n".join(f"- {f}" for f in facts)


def database_layout():
    """Tell a specialist which collections and fields exist, so it doesn't have to guess."""
    try:
        layout = json.loads(run_query({"op": "layout"}))
        if not isinstance(layout, dict):
            raise ValueError("unexpected layout result")
    except (QueryError, ValueError) as e:
        return f"DATABASE: not available right now ({str(e)[:120]}). Say the records can't be checked."
    lines = [f"- {coll}, for example: {json.dumps(example, default=str)[:400]}" for coll, example in layout.items()]
    return ("DATABASE LAYOUT: each collection with one example record. Use these exact collection and "
            "field names; you don't need to call collections or sample first.\n" + "\n".join(lines) +
            "\nTips: a request can have several records in one collection (e.g. one budget per year), so "
            "don't use limit 1. Follow links between collections (e.g. a request's opportunity_id). "
            'Match text loosely, e.g. {"kind": {"$regex": "IRB", "$options": "i"}}.')


# ============================================================================ grant teams
# The people "data file" (people.json) is the directory the assistant looks people up in. Teams are
# kept in the team collection. Adding a confirmed person to a team is the only write the AI can make,
# and the code checks it: the person must be in people.json and the role must be a known one.

PEOPLE_FILE = next((p for p in (HERE / "people.json", HERE.parent / "people.json") if p.exists()),
                   HERE.parent / "people.json")
TEAM_ROLES = ("PI", "Co-Investigator", "Collaborator")


def load_people():
    try:
        return json.loads(PEOPLE_FILE.read_text(encoding="utf-8"))["people"]
    except (OSError, ValueError, KeyError):
        return []


def find_person(query):
    """People in people.json whose name, id, email or department matches the query."""
    q = str(query).strip().lower().replace("dr.", "").strip()
    words = [w for w in re.split(r"\s+", q) if w]
    matches = [p for p in load_people()
               if words and all(any(w in str(p.get(f, "")).lower() for f in ("name", "id", "email", "department"))
                                for w in words)]
    return json.dumps(matches) if matches else f"No one matching {query!r} is in the people file."


def list_team(request_id):
    try:
        team = run_query({"op": "find", "collection": "team", "filter": {"request_id": request_id},
                          "projection": {"_id": 0}})
    except QueryError as e:
        return f"ERROR: {e}"
    return team if team.strip() not in ("", "[]") else f"No one is on the team for {request_id} yet."


def add_team_member(request_id, person_id, role, invited_by=""):
    """Add one confirmed person to a request's team (created if the request is new)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", str(request_id)):
        return "ERROR: the request id must look like SAMPLE-001."
    role = next((r for r in TEAM_ROLES if r.lower() == str(role).strip().lower()), None)
    if not role:
        return f"ERROR: role must be one of {', '.join(TEAM_ROLES)}."
    person = next((p for p in load_people() if p["id"] == person_id), None)
    if not person:
        return f"ERROR: no person with id {person_id!r} in the people file. Look them up with find_person first."
    if role == "PI" and not person.get("can_be_pi"):
        return f"ERROR: {person['name']} ({person['title']}) can't be a PI according to the people file."
    member = {"request_id": request_id, "person_id": person["id"], "name": person["name"], "role": role,
              "title": person["title"], "department": person["department"], "institution": person["institution"],
              "email": person["email"], "invited_by": invited_by or ""}
    script = r"""
const a = EJSON.parse(process.env.JUDGE_ARGS);
const d = db.getSiblingDB(process.env.JUDGE_DB);
if (d.team.findOne({request_id: a.m.request_id, person_id: a.m.person_id})) { print("exists"); }
else {
  d.team.insertOne(Object.assign({added_at: new Date()}, a.m));
  if (!d.requests.findOne({request_id: a.m.request_id}))
    d.requests.insertOne({request_id: a.m.request_id, pi: "(not set)", status: "Draft", items: []});
  print("added");
}
"""
    try:
        env = {**os.environ, "JUDGE_ARGS": json.dumps({"m": member}), "JUDGE_DB": write_db()}
    except PermissionError as e:
        return f"ERROR: {e}"
    proc = subprocess.run([MONGOSH, setting("mongo_url"), "--quiet", "--norc", "--eval", script],
                          capture_output=True, text=True, timeout=30, env=env)
    if proc.returncode != 0:
        return "ERROR: couldn't save: " + (proc.stderr or proc.stdout).strip()[-200:]
    if proc.stdout.strip().endswith("exists"):
        return f"{person['name']} is already on the team for {request_id}."
    return f"Added {person['name']} to {request_id} as {role}. They now appear on the Collaboration page."


TEAM_TOOLS = {
    "find_person": ('look up people in the people file by name, email or department, to get their details and '
                    'id. args: {"query": str}', lambda args: find_person(args.get("query", ""))),
    "list_team": ('list who is on a request\'s team (PIs, investigators, collaborators). args: {"request_id": str}',
                  lambda args: list_team(args.get("request_id", ""))),
    "add_team_member": ('add ONE person to a request\'s team, ONLY after the user has confirmed the person and role. '
                        'args: {"request_id": str, "person_id": str (from find_person), "role": "PI" | '
                        '"Co-Investigator" | "Collaborator", "invited_by": str (who brought them in, if anyone)}',
                        lambda args: add_team_member(args.get("request_id", ""), args.get("person_id", ""),
                                                     args.get("role", ""), args.get("invited_by", ""))),
}


# ============================================================================ CV check
# Who on the grant team has submitted a CV: the team is samples/affiliation_sample1.csv (from the
# professor's Demo Plan) and a CV counts as submitted when a file with the person's last name is in
# samples/cvs/ (his Data_Samples/CVs). To use other files, set TEAM_CSV and CV_DIR.

SAMPLES = HERE / "samples" if (HERE / "samples").is_dir() else HERE.parent / "samples"


def cv_status():
    import csv
    team_file = Path(os.environ.get("TEAM_CSV") or SAMPLES / "affiliation_sample1.csv")
    cv_dir = Path(os.environ.get("CV_DIR") or SAMPLES / "cvs")
    with team_file.open(encoding="utf-8-sig", newline="") as f:
        team = [{"name": (r.get("Name") or "").strip(), "email": (r.get("Email") or "").strip(),
                 "affiliation": (r.get("Affiliation") or "").strip()} for r in csv.DictReader(f) if (r.get("Name") or "").strip()]
    files = sorted(p.name for p in cv_dir.iterdir() if p.is_file()) if cv_dir.is_dir() else []
    last = lambda name: re.findall(r"[a-z]+", name.lower())[-1]
    submitted, missing = [], []
    for person in team:
        cv = next((f for f in files if re.findall(r"[a-z]+", f.lower())[:1] == [last(person["name"])]), None)
        (submitted if cv else missing).append({**person, "cv_file": cv})
    on_team = {p["cv_file"] for p in submitted}
    return json.dumps({"team_size": len(team), "submitted": submitted, "not_submitted": missing,
                       "cv_files_not_matching_anyone_on_the_team": [f for f in files if f not in on_team]},
                      ensure_ascii=False)


# Tools an agent file can ask for by name in its "tools" list.
NAMED_TOOLS = {**TEAM_TOOLS, **LIBRARY_TOOLS,
               "cv_status": ("who on the grant team has and has not submitted a CV, with each CV file name. args: {}",
                             lambda args: cv_status())}


def _named_tools(spec):
    unknown = [t for t in spec.get("tools", []) if t not in NAMED_TOOLS]
    if unknown:
        raise SystemExit(f"Unknown tool name(s) {unknown} in an agent file. Known tools: {sorted(NAMED_TOOLS)}")
    return {t: NAMED_TOOLS[t] for t in spec.get("tools", [])}


class Judge:
    """The agent the user talks to. It remembers the conversation and has one ask_<name> tool
    per LLM in agents/llms/, plus the team tools (find_person, list_team, add_team_member)."""

    def __init__(self, on_event=print):
        self.on_event = on_event   # where progress steps go; the server swaps it per request
        self.message = ""          # the user's current message, passed on to specialists
        agents = load_agents()
        tools = {f"ask_{name}": (f'ask the {name} specialist about {spec["description"]}. args: {{"question": str}}',
                                 lambda args, name=name: ask_specialist(name, args.get("question", ""),
                                                                        self._event, self.message))
                 for name, spec in agents["specialists"].items()}
        tools.update(TEAM_TOOLS)
        instructions = (agents["judge"]["instructions"] + agents["judge"].get("guided_flow", [])
                        + agents["settings"].get("reply_style", []))
        self.agent = Agent("judge", agents["judge"]["model"], instructions, tools, self._event)

    def _event(self, text):
        self.on_event(text)

    def reply(self, message):
        self.message = message
        return self.agent.ask(message)


# ============================================================================ adding a document
# The only code that writes to the database. People run it; the LLMs can't.

SECTION_NAMES = ("abstract", "project summary", "specific aims", "research strategy", "significance",
                 "innovation", "approach", "budget justification", "budget", "biosketch",
                 "human subjects", "vertebrate animals", "bibliography", "references", "facilities",
                 "letters of support", "data management plan", "title")


def read_file_text(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix == ".docx":
        from docx import Document
        # Word's Heading/Title styles mark sections, so treat them like '# ' headings.
        return "\n".join(("# " if p.style.name.startswith(("Heading", "Title")) else "") + p.text
                         for p in Document(path).paragraphs)
    return path.read_text(encoding="utf-8", errors="replace")


def split_sections(text):
    """Split a grant into sections at heading lines ('# Budget', 'SPECIFIC AIMS', 'Budget Justification:')."""
    sections, name, lines = [], "Introduction", []
    for line in text.splitlines():
        heading = line.strip().lstrip("#").strip().rstrip(":").strip()
        if heading and len(heading) < 60 and (line.lstrip().startswith("#") or heading.lower() in SECTION_NAMES):
            if "\n".join(lines).strip():
                sections.append((name, "\n".join(lines).strip()))
            name, lines = heading.title() if heading.isupper() else heading, []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        sections.append((name, "\n".join(lines).strip()))
    return sections


def read_docx_formatting(path):
    """The fonts, font sizes (pt) and margins (inches) of a Word file's body text (not headings)."""
    from docx import Document
    doc = Document(path)
    normal = doc.styles["Normal"].font
    fonts, sizes = set(), set()
    for p in doc.paragraphs:
        if not p.text.strip() or p.style.name.startswith(("Heading", "Title")):
            continue
        for run in p.runs:
            fonts.add(run.font.name or p.style.font.name or normal.name or "Calibri")
            size = run.font.size or p.style.font.size or normal.size
            sizes.add(size.pt if size else 11.0)
    margins = {}
    for side in ("top", "bottom", "left", "right"):
        values = [getattr(s, f"{side}_margin") for s in doc.sections if getattr(s, f"{side}_margin") is not None]
        margins[side] = round(min(v.inches for v in values), 2) if values else 1.0
    return {"fonts": sorted(fonts), "font_sizes": sorted(sizes), "margins_in": margins}


def add_document(file_path, request_id):
    """Store a file's sections in the documents collection, linked to request_id. Replaces an
    earlier upload of the same file for that request, and creates the request if it's new."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", request_id):
        raise SystemExit("The request id must look like SAMPLE-001 (letters, numbers, - or _).")
    name = Path(file_path).name
    docs = [{"request_id": request_id, "document": name, "section": s, "order": i + 1,
             "pages": round(len(t.split()) / 500, 1), "text": t}
            for i, (s, t) in enumerate(split_sections(read_file_text(file_path)))]
    if name.lower().endswith(".docx"):
        fmt = read_docx_formatting(file_path)
        docs.append({"request_id": request_id, "document": name, "section": "Formatting", "order": 0,
                     "formatting": fmt, "text": f"Fonts {', '.join(fmt['fonts'])}; sizes "
                     f"{', '.join(str(s) for s in fmt['font_sizes'])} pt; margins {fmt['margins_in']} inches"})
    script = r"""
const a = EJSON.parse(process.env.JUDGE_ARGS);
const d = db.getSiblingDB(process.env.JUDGE_DB);
d.documents.deleteMany({request_id: a.request_id, document: a.name});
if (a.docs.length) d.documents.insertMany(a.docs);
if (!d.requests.findOne({request_id: a.request_id}))
  d.requests.insertOne({request_id: a.request_id, pi: "(not set)", status: "Draft", items: []});
print("ok");
"""
    try:
        env = {**os.environ, "JUDGE_ARGS": json.dumps({"request_id": request_id, "name": name, "docs": docs}),
               "JUDGE_DB": write_db()}
    except PermissionError as e:
        raise SystemExit(str(e))
    proc = subprocess.run([MONGOSH, setting("mongo_url"), "--quiet", "--norc", "--eval", script],
                          capture_output=True, text=True, timeout=60, env=env)
    if proc.returncode != 0:
        raise SystemExit("Couldn't save it: " + (proc.stderr or proc.stdout).strip()[-300:])
    print(f"Saved {name} to {request_id} as {len(docs)} sections:")
    for doc in docs:
        print(f"  - {doc['section']}: " + (doc["text"] if "formatting" in doc else f"~{doc['pages']} pages"))
    print(f'Now ask the assistant, for example: "Summarize the {name} for {request_id} in 5 sentences."')


# ============================================================================ commands

def all_models():
    agents = load_agents()
    return list(dict.fromkeys([agents["judge"]["model"], *(s["model"] for s in agents["specialists"].values())]))


def setup_demo():
    """Load the made-up SAMPLE-001..005 grants into OUR database (settings.mongo_db) only.
    Refuses a protected team database, and any database holding real (non-SAMPLE) requests."""
    try:
        name = write_db()
    except PermissionError as e:
        raise SystemExit(str(e))
    root = Path(__file__).resolve().parent
    root = root if (root / "demo_data.js").exists() else root.parent
    env = {**os.environ, "JUDGE_DB": name}
    check = subprocess.run([MONGOSH, setting("mongo_url"), "--quiet", "--norc", "--eval",
                            "print(db.getSiblingDB(process.env.JUDGE_DB).requests"
                            ".countDocuments({request_id: {$not: /^SAMPLE-/}}))"],
                           capture_output=True, text=True, timeout=30, env=env)
    if check.returncode != 0:
        raise SystemExit("Can't reach the database: " + (check.stderr or check.stdout).strip()[-300:])
    real = int(check.stdout.strip().splitlines()[-1])
    if real:
        raise SystemExit(f"Database {name!r} has {real} real (non-SAMPLE) requests. Not touching it.")
    print(f"Loading demo grants SAMPLE-001 to SAMPLE-005 into database {name!r} ...")
    load = subprocess.run([MONGOSH, setting("mongo_url"), "--quiet", "--norc", "--eval",
                           "db = db.getSiblingDB(process.env.JUDGE_DB); load(process.env.DEMO_FILE)"],
                          capture_output=True, text=True, timeout=60,
                          env={**env, "DEMO_FILE": str(root / "demo_data.js")})
    if load.returncode != 0:
        raise SystemExit("Loading failed: " + (load.stderr or load.stdout).strip()[-300:])
    add_document(root / "samples" / "good_grant.docx", "SAMPLE-004")
    add_document(root / "samples" / "bad_grant.docx", "SAMPLE-005")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if command == "pull":
        for model in all_models():
            if model.startswith("claude"):
                print(f"{model}: runs through your Claude login (claude auth login), nothing to download")
                continue
            print(f"Downloading {model} (can take several minutes) ...", flush=True)
            r = httpx.post(f"{setting('ollama_url')}/api/pull", json={"model": model, "stream": False}, timeout=None)
            print(f"  {model}: {r.json().get('status', r.text)}")
    elif command == "check-db":
        print("Collections:", run_query({"op": "collections"}))
    elif command == "add-document":
        if len(sys.argv) != 4:
            raise SystemExit('Usage: python judge.py add-document "C:\\path\\to\\grant.docx" SAMPLE-001')
        add_document(sys.argv[2], sys.argv[3])
    elif command == "setup-demo":
        setup_demo()
    elif command == "chat":
        judge = Judge(on_event=lambda text: print(f"  [{text}]"))
        print("Grant Judge (type 'quit' to leave). Replies can take a minute.")
        while (message := input("\nYou> ").strip()).lower() not in ("quit", "exit"):
            if message:
                try:
                    print("\nJudge> " + judge.reply(message))
                except LLMError as e:
                    print(f"\n[problem] {e}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
