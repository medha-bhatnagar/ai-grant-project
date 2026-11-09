# ai-grant-project (with the AI Judge)

**New here? Read [START_HERE.md](START_HERE.md)** to run it on your own PC in a few steps.

The dashboard's Assistant chat talks to an AI Judge, which hands questions to specialist agents.
Each agent uses the model set in agents.json (Claude by default, or a local Ollama model), and
specialists can read MongoDB (read-only).

## The files

```
agents.json          ← EVERY AGENT: the Judge and each specialist (model, instructions, database access)
compose.yaml         ← runs it on the VM (3 containers: dashboard, judge, llm)
run_local.ps1        ← runs it on a Windows PC instead (no Docker)
dashboard/           ← the web UI (index.html, plus nginx.conf and Dockerfile)
judge/
  judge.py           ← the engine that runs agents.json (nobody needs to edit this)
  server.py          ← connects the dashboard's chat to the Judge
  test_judge.py      ← tests (no AI needed)
  Dockerfile, requirements.txt
```

## Change what the agents do: edit agents.json

- **Swap a model:** change its `"model"`. New models download with the pull command below.
- **Change behaviour:** edit its `"instructions"` (one sentence per line).
- **Add a specialist:** copy an entry under `"specialists"` and rename it. The Judge gets an
  `ask_<name>` tool for it automatically.
- **Database address:** `"settings"`. Put a password only in the `MONGO_URL` environment variable.

Changes apply to the next chat message; no restart or rebuild needed.

## Run on the VM (Docker)

    docker compose up -d --build                    # first time: a few minutes
    docker compose exec judge python judge.py pull  # download the models (~13 GB, first time)
    docker compose exec judge python judge.py check-db

Then from your PC: `ssh -L 8080:localhost:8080 cjw22003@uchcgrantai.cse.uconn.edu`
and open http://localhost:8080. Other commands: `docker compose logs -f judge` (watch it work),
`docker compose exec judge python judge.py chat` (terminal chat), `docker compose down` (stop).

## Run on a Windows PC

Install the Ollama app and mongosh, start Ollama, then:

    powershell -ExecutionPolicy Bypass -File run_local.ps1

and open http://localhost:8080. For the VM's database, keep this open in another window:
`ssh -L 27017:localhost:27017 cjw22003@uchcgrantai.cse.uconn.edu`

## Demo data

`demo_data.js` holds made-up records matching the dashboard's SAMPLE-001 to SAMPLE-005
(requirements, budgets, deadlines, compliance documents, policies). Load it into a local
MongoDB, never the team's real database, since it replaces those collections. Then add the two
sample grant files: `samples/good_grant.docx` (SAMPLE-004, complete and within limits) and
`samples/bad_grant.docx` (SAMPLE-005, missing sections, placeholders, over budget):

    mongosh mongodb://localhost:27017/grants demo_data.js
    python judge/judge.py add-document samples/good_grant.docx SAMPLE-004
    python judge/judge.py add-document samples/bad_grant.docx SAMPLE-005

## Adding your own grant file

    python judge/judge.py add-document "C:\path\to\grant.docx" SAMPLE-006

Works with .docx, .pdf, .txt and .md. Sections are split at headings (Word heading styles, or
lines like "Specific Aims"). A new request id is created if it doesn't exist yet.

Good demo questions: "What's still missing for this request?", "Is this budget within the
limit?" (SAMPLE-003 is over), "What's due this week?", "Who needs to act on the IRB application?"
