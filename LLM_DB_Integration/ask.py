import json
from datetime import datetime
import numpy as np
import requests
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer
import os

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/?directConnection=true")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

CHAT_FILE = "chat_history.json"

if os.path.exists(CHAT_FILE):
    with open(CHAT_FILE) as f:
        history = json.load(f)
    print(f"Loaded {len(history)} past questions from {CHAT_FILE}")
else:
    history = []

print("Loading...")
db = MongoClient(MONGO_URL)["grant_rag"]
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

chunks = []
for name in ["announcements", "applications", "past_submissions"]:
    chunks += list(db[name].find({}, {"text": 1, "doc_link": 1, "embedding": 1}))
vectors = np.array([c["embedding"] for c in chunks])
print(f"Ready. {len(chunks)} chunks loaded. Type a question, or 'quit' to exit.\n")

while True:
    question = input("> ").strip()
    if question.lower() in ("quit", "exit"):
        break
    if not question:
        continue

    # find the 5 most relevant chunks
    q = embedder.encode(question, normalize_embeddings=True)
    best = np.argsort(vectors @ q)[::-1][:5]
    context = "\n\n".join(f"[{chunks[i]['doc_link']}]\n{chunks[i]['text']}" for i in best)

    prompt = f"""Answer the question using only these excerpts. Say which document each fact
came from. If the answer isn't in the excerpts, say so.

EXCERPTS:
{context}

QUESTION: {question}"""
    past_messages = []
    for h in history[-3:]:
        past_messages.append({"role": "user", "content": h["question"]})
        past_messages.append({"role": "assistant", "content": h["answer"]})

    answer = ""
    with requests.post(f"http://{OLLAMA_URL}/api/chat", json={
        "model": "llama3.1:8b",
        "messages": past_messages + [{"role": "user", "content": prompt}],
        "stream": True,
        "keep_alive": "30m",
        "options": {"num_ctx": 4096},
    }, stream=True) as resp:
        for line in resp.iter_lines():
            if line:
                piece = json.loads(line)["message"]["content"]
                print(piece, end="", flush=True)
                answer += piece
    print("\n")

    history.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "answer": answer,
        "sources": [chunks[i]["doc_link"] for i in best],
    })
    with open(CHAT_FILE, "w") as f:
        json.dump(history, f, indent=2)