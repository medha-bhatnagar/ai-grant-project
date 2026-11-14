# ai-grant-project (with the AI Judge)

**New here? Read [START_HERE.md](START_HERE.md)** to run it on your own PC in a few steps.

The dashboard's Assistant chat talks to an AI Judge, which hands questions to specialist agents.
Each agent is a small file in the `agents/` folder (Claude by default, or a local Ollama model),
and specialists can read MongoDB (read-only).

## The files

```
agents/              ← THE AI: edit these to change it (see agents/README.md)
  judge.json         ← the Judge
  llms/              ← one file per LLM: add a file to add one, delete it to remove one
  settings.json      ← database address, reply style
compose.yaml         ← runs it on the VM (dashboard + judge containers)
run_local.ps1        ← runs it on a Windows PC instead (no Docker)
dashboard/           ← the web UI (index.html, plus nginx.conf and Dockerfile)
judge/               ← the engine (nobody needs to edit this)
```

## Change what the agents do

See [agents/README.md](agents/README.md). In short: copy `agents/llms/_TEMPLATE.json` to add an
LLM, delete a file to remove one, and edit `instructions` to change one. Changes apply to the next
chat message, with no restart or rebuild. Put a database password only in the `MONGO_URL`
environment variable, never in a file.

## Run on the VM (Docker)

Full steps (and how to undo them) are in [VM_CHANGES.md](VM_CHANGES.md). In short:

    cp .env.example .env                            # then put your Claude token in it
    docker compose up -d --build                    # first time: a few minutes
    docker compose exec judge python judge.py setup-demo   # sample grants, our "grants" database only

Then from your PC: `ssh -L 8090:localhost:8090 cjw22003@uchcgrantai.cse.uconn.edu`
and open http://localhost:8090. Other commands: `docker compose logs -f judge` (watch it work),
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
