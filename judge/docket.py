"""The collaboration docket for the Monday demo (the professor's Demo Plan).

A docket is one grant application's team: who the investigators are (from the affiliation file),
which documents each one has uploaded (their CV/biosketch, abstract, ...), their research security
training (from compliance.csv), and the emails SPS sent them (a demo outbox: nothing is really sent).

Everything is kept in one JSON file plus the uploaded files, in DOCKET_DIR (default: data/docket,
which git ignores). No database. The people data comes from the CSVs in demo/.
"""
import csv
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROOT = ROOT if (ROOT / "demo").is_dir() else ROOT.parent
DEMO_DIR = ROOT / "demo"
DOCKET_DIR = Path(os.environ.get("DOCKET_DIR", ROOT / "data" / "docket"))
DOC_TYPES = ["CV / Biosketch", "Abstract", "Specific Aims", "Research Strategy", "Budget Justification",
             "Facilities", "Equipment", "Human Subjects", "Vertebrate Animals", "Narrative", "Other"]
CV = DOC_TYPES[0]
_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- people (the professor's CSVs)

def _rows(name):
    path = DEMO_DIR / name
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k and k.strip()}
                for row in csv.DictReader(f) if any((v or "").strip() for v in row.values() if isinstance(v, str))]


def _words(name):
    return [w for w in re.split(r"[^a-z]+", name.lower()) if w]


def same_person(a, b):
    """'Dong-Guk Shin' matches 'Dong Shin', 'Dr. Creech' matches 'C.Creech': same last name, and the
    first initials agree when both names have one."""
    wa, wb = [w for w in _words(a) if w not in ("dr", "prof")], [w for w in _words(b) if w not in ("dr", "prof")]
    if not wa or not wb or wa[-1] != wb[-1]:
        return False
    return len(wa) == 1 or len(wb) == 1 or wa[0][0] == wb[0][0]


def directory():
    """Everyone in the affiliation files (affiliation_sample1.csv first, then affiliation_sample.csv)."""
    people = []
    for file in ("affiliation_sample1.csv", "affiliation_sample.csv"):
        for r in _rows(file):
            if r.get("Name") and not any(same_person(r["Name"], p["name"]) for p in people):
                people.append({"name": r["Name"], "email": r.get("Email", ""), "phone": r.get("Phone", ""),
                               "affiliation": r.get("Affiliation", ""), "netid": r.get("Netid", ""), "source": file})
    return people


def find_affiliation(name):
    match = next((p for p in directory() if same_person(name, p["name"])), None)
    return match or {"name": name.strip(), "email": "", "phone": "", "affiliation": "", "netid": "", "source": ""}


def training_status(person):
    """'yes', 'no' or 'no record', from compliance.csv. Matched by name, or by the same email address
    (the professor's files reuse addresses across the two affiliation samples)."""
    rows = _rows("compliance.csv")
    emails = {p["name"]: p["email"].lower() for p in directory() if p["email"]}
    for r in rows:
        name = r.get("Name", "")
        if same_person(name, person["name"]) or (
                person.get("email") and any(same_person(name, n) and e == person["email"].lower()
                                            for n, e in emails.items())):
            value = (r.get("annual research security training") or "").lower()
            return value if value in ("yes", "no") else "no record"
    return "no record"


# ---------------------------------------------------------------- the docket file

def _path():
    return DOCKET_DIR / "docket.json"


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"dockets": [], "outbox": []}


def _save(data):
    DOCKET_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(_path())


def _find(data, docket_id=None):
    """The docket with this id (any capitalization), or the newest one when no id is given."""
    if not data["dockets"]:
        return None
    if not docket_id:
        return data["dockets"][-1]
    return next((d for d in data["dockets"] if d["id"].lower() == str(docket_id).strip().lower()), None)


def create_docket(docket_id, names, created_by=""):
    """Start (or replace) the docket for an application with these investigators, filled in from the
    affiliation file. Uploaded documents of people who stay on the team are kept."""
    docket_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(docket_id or "").strip()).strip("-")[:60] or "DOCKET-1"
    names = [n.strip() for n in names if n and n.strip()][:30]
    if not names:
        raise ValueError("give at least one investigator name")
    team = []
    for n in names:
        person = find_affiliation(n)
        if not any(same_person(person["name"], t["name"]) for t in team):
            team.append({**person, "found": bool(person["source"])})
    with _lock:
        data = load()
        old = _find(data, docket_id)
        docs = [d for d in (old or {}).get("documents", []) if any(same_person(d["investigator"], t["name"]) for t in team)]
        data["dockets"] = [d for d in data["dockets"] if d is not old] + [
            {"id": docket_id, "created_at": _now(), "created_by": created_by, "investigators": team, "documents": docs}]
        _save(data)
    return status(docket_id)


def add_document(docket_id, investigator, doc_type, original_name, content, uploaded_by=""):
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {DOC_TYPES}")
    suffix = Path(original_name).suffix.lower()
    if suffix not in (".pdf", ".doc", ".docx"):
        raise ValueError("only PDF or Word files (.pdf, .doc, .docx)")
    with _lock:
        data = load()
        docket = _find(data, docket_id)
        if not docket:
            raise ValueError(f"no docket named {docket_id!r}")
        person = next((p for p in docket["investigators"] if same_person(investigator, p["name"])), None)
        if not person:
            raise ValueError(f"{investigator!r} is not on the {docket['id']} docket")
        doc_id = uuid.uuid4().hex[:12]
        (DOCKET_DIR / "files").mkdir(parents=True, exist_ok=True)
        (DOCKET_DIR / "files" / f"{doc_id}{suffix}").write_bytes(content)
        docket["documents"].append({"id": doc_id, "investigator": person["name"], "doc_type": doc_type,
                                    "file": f"{doc_id}{suffix}", "original_name": Path(original_name).name[:200],
                                    "size": len(content), "uploaded_by": uploaded_by, "uploaded_at": _now()})
        _save(data)
    return doc_id


def document_file(doc_id):
    for docket in load()["dockets"]:
        for d in docket["documents"]:
            if d["id"] == doc_id:
                return DOCKET_DIR / "files" / d["file"], d["original_name"]
    return None, None


def status(docket_id=None):
    """Everything about one docket, with each investigator's documents, CV and training status."""
    data = load()
    docket = _find(data, docket_id)
    if not docket:
        return None
    people = []
    for p in docket["investigators"]:
        docs = [d for d in docket["documents"] if d["investigator"] == p["name"]]
        people.append({**p, "documents": docs, "cv": any(d["doc_type"] == CV for d in docs),
                       "training": training_status(p)})
    return {"id": docket["id"], "created_at": docket["created_at"], "created_by": docket.get("created_by", ""),
            "investigators": people, "outbox": [m for m in data["outbox"] if m["docket"] == docket["id"]]}


def everything():
    data = load()
    return {"dockets": [status(d["id"]) for d in data["dockets"]], "outbox": data["outbox"], "doc_types": DOC_TYPES}


# ---------------------------------------------------------------- emails (demo outbox)

def cv_request(person, docket_id, sender):
    last = _words(person["name"])[-1].capitalize() if _words(person["name"]) else person["name"]
    return {"to_name": person["name"], "to_email": person.get("email", ""),
            "subject": f"CV / biosketch needed for the {docket_id} application",
            "body": (f"Dear Dr. {last},\n\n"
                     f"SPS is preparing the routing package for the {docket_id} application, and we have not "
                     f"yet received your CV (NIH biosketch). Could you please upload it to the Grant Application "
                     f"Assistant portal (Docket tab) at your earliest convenience?\n\n"
                     f"Thanks,\n{sender or 'SPS'}")}


def draft_cv_requests(docket_id=None, sender=""):
    s = status(docket_id)
    if not s:
        return None
    return [cv_request(p, s["id"], sender) for p in s["investigators"] if not p["cv"]]


def send_emails(docket_id, emails, sender=""):
    """Put emails in the demo outbox. Nothing leaves the app."""
    with _lock:
        data = load()
        docket = _find(data, docket_id)
        if not docket:
            raise ValueError(f"no docket named {docket_id!r}")
        sent = []
        for e in emails[:20]:
            msg = {"id": uuid.uuid4().hex[:12], "docket": docket["id"], "to_name": str(e.get("to_name", ""))[:120],
                   "to_email": str(e.get("to_email", ""))[:200], "subject": str(e.get("subject", ""))[:300],
                   "body": str(e.get("body", ""))[:5000], "sender": sender, "sent_at": _now()}
            data["outbox"].append(msg)
            sent.append(msg)
        _save(data)
    return sent


def reset():
    """Empty the demo: no dockets, no outbox (uploaded files are deleted too)."""
    with _lock:
        for f in (DOCKET_DIR / "files").glob("*") if (DOCKET_DIR / "files").is_dir() else []:
            f.unlink()
        _save({"dockets": [], "outbox": []})
