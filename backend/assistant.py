"""The AI assistant inside the signed-in app: POST /api/chat and GET /api/team.

The Judge and its LLMs live in ../judge (the engine) and ../agents (one file per LLM). This file only
connects them to the sign-in: both routes need a signed-in user, and each user gets their own chat
memory, so nobody can read or continue someone else's conversation.
"""
import sys
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))
import server as judge_server  # noqa: E402  (the Judge's chat code: sessions, logging, errors)

router = APIRouter(prefix="/api", tags=["assistant"])
signed_in = __import__(__package__ + ".main", fromlist=["current_user"]).current_user   # same check as collaboration.py


class ChatIn(BaseModel):
    session_id: str = "default"
    message: str
    context: str = ""   # what the user is looking at, e.g. "SAMPLE-001 ([PI name 1])"


@router.post("/chat")
def chat(body: ChatIn, user=Depends(signed_in)):
    session = f"{user['login_id']}:{body.session_id}"[:200]
    return judge_server.chat(judge_server.ChatIn(session_id=session, message=body.message, context=body.context))


@router.get("/team")
def team(user=Depends(signed_in)):
    return judge_server.team()
