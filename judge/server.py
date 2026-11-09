"""Web API for the dashboard's Assistant chat: POST /chat. One Judge (with memory) per session.

In Docker:   uvicorn server:app      (nginx forwards the dashboard's /api/ here)
On a PC:     python server.py        (serves the dashboard too, at http://localhost:8080)
"""
import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from judge import MAX_DOCS, Judge, LLMError, QueryError, load_agents, run_query

app = FastAPI(title="Grant Judge")
sessions = {}


class ChatIn(BaseModel):
    session_id: str = "default"
    message: str
    context: str = ""   # what the user is looking at, e.g. "SAMPLE-001 ([PI name 1])"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/team")
def team():
    """Everyone on every request's team, for the dashboard's Collaboration page."""
    try:
        return {"team": json.loads(run_query({"op": "find", "collection": "team", "filter": {},
                                               "projection": {"_id": 0}, "limit": MAX_DOCS}))}
    except (QueryError, ValueError) as e:
        raise HTTPException(status_code=503, detail=f"Can't read the team list: {e}")


def log(text):
    """Print a step in the server window, so you can watch the Judge and specialists work."""
    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)


# Windows consoles can't print emoji by default; without this, a reply containing ❌ would crash.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


@app.post("/chat")
def chat(body: ChatIn):
    steps = []
    if body.session_id not in sessions:
        sessions[body.session_id] = Judge()
    judge = sessions[body.session_id]
    judge.on_event = lambda text: (steps.append(text), log("   " + text))
    message = f"(The user is viewing: {body.context})\n{body.message}" if body.context else body.message
    log(f'USER asks: "{body.message}"' + (f"  (viewing {body.context})" if body.context else ""))
    start = time.time()
    try:
        reply = judge.reply(message)
    except LLMError as e:
        log(f"   problem: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    log(f"JUDGE replies after {time.time() - start:.0f} s: {reply[:150]}{'...' if len(reply) > 150 else ''}\n")
    return {"reply": reply, "steps": steps}


if __name__ == "__main__":
    import uvicorn
    from fastapi.responses import FileResponse

    dashboard = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
    site = FastAPI()
    site.mount("/api", app)
    site.get("/")(lambda: FileResponse(dashboard))
    agents = load_agents()
    print("Grant Application Assistant: http://localhost:8080   (Ctrl+C to stop)\n\nAgents (from agents.json):")
    print(f"  judge         -> {agents['judge']['model']}")
    for name, spec in agents["specialists"].items():
        print(f"  {name:<13} -> {spec['model']}")
    print("\nEach chat message and every step the agents take will appear below.\n")
    uvicorn.run(site, host="127.0.0.1", port=8080)
