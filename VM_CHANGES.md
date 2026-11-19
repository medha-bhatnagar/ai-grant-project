# Changes for running on the team VM (and how to undo each one)

Starting point before these changes: commit `7321454` on branch `judge-agent`
(our work merged with the team's latest `main`).

Each step below is its own git commit. To undo one step, run `git revert <commit>`.
To go back to before all of them: `git reset --hard 7321454` (this throws away later work, so be sure).

Nothing here touches the VM, the team's databases, or other people's folders. These are code
changes on Colin's PC until the files are copied to `~/Team44` on the VM.

## Step 1: never write to the team's databases

- `judge/judge.py`
  - New `PROTECTED_DBS` = grant_rag, affiliation, admin, local, config.
  - New `write_db()`. Both places that write (`add_team_member`, `add_document`) now go through it,
    and it refuses any protected database.
  - New command `python judge.py setup-demo`. It loads the SAMPLE grants into our database only
    (`mongo_db`, default `grants`). It refuses protected databases and any database holding
    non-SAMPLE requests. It works the same on Windows, the VM, and inside Docker.
- `judge/test_judge.py`: new test `test_team_databases_are_never_written`.
- Reading is unchanged: the Judge can still read any database (read-only checks as before).
- Tested: 16 tests pass. `setup-demo` loaded `grants` on the PC, and `MONGO_DB=grant_rag` was refused.

## Step 2: Docker setup that can't clash with teammates

- `compose.yaml`
  - `name: colin-judge` gives it its own project name, so it can't replace anyone's containers.
  - Ports default to **8090** (website) and **8010** (judge), not 8080/8000. They're settable in `.env`.
  - `MONGO_DB` defaults to `grants`.
  - `CLAUDE_CODE_OAUTH_TOKEN` is passed from `.env`.
  - `people.json` is mounted alongside `agents.json`.
  - The Ollama `llm` service is now **off unless asked for** (`--profile ollama`), so it can't fight
    a teammate's Ollama on port 11434. The judge no longer waits for it.
- `judge/Dockerfile`
  - Installs Claude Code **2.1.319** (the version tested on the PC).
  - Copies `people.json`, `demo_data.js` and the sample grants, which were missing before, so the
    team tools and `setup-demo` work in the container.
  - The port comes from `JUDGE_PORT`.
- `dashboard/Dockerfile` and `dashboard/nginx.conf`: the ports come from compose (nginx's template
  feature) instead of the fixed 8080/8000.
- New `.env.example`: the settings to copy into `.env` on the VM (`.env` is git-ignored).
- **Not tested yet:** Docker isn't installed on the PC, so the containers have only been built on
  paper. The first real build is on the VM (steps below).

## Step 3: Linux line endings

- New `.gitattributes`: files get Linux line endings (LF) in git and in the zip; `.ps1` scripts keep
  Windows ones. Without it, the zip had Windows line endings, which break `.env` values and shell
  commands on the VM. It doesn't change any file's content.

## Step 4: read the team's past grants (read only)

The VM's MongoDB has a teammate's database `team44_shc22039_demo`, with a `past_submissions`
collection: 60 chunks of real NIH sample applications (title, PI, page number, text).

- `agents.json`
  - New settings `library_db` (`team44_shc22039_demo`) and `library_collection` (`past_submissions`).
  - New `past_grants` specialist: lists, summarizes and searches the past grants, naming the grant
    and page it relied on.
  - One new Judge rule: questions about past or sample grants in the library go to `past_grants`.
- `judge/judge.py`
  - Three new **read-only** query types: `library_list`, `library_read`, `library_search`. Search
    words are matched as plain text, never as a pattern.
  - `run_query` can read a second database. New tools `list_past_grants`, `read_past_grant` and
    `search_past_grants`.
  - `write_db()` now also refuses the library database, so nothing is ever written there.
- `judge/test_judge.py`: 2 new tests (18 total, all pass).
- Tested on the PC with a made-up stand-in library (`library_test`, deleted afterwards). Claude listed
  the grants, summarized one with page numbers, and SAMPLE-001 questions still worked.
- Not yet tested against the real `team44_shc22039_demo` on the VM.

## Step 5: one file for the Judge, one file per LLM

`agents.json` was split into an `agents/` folder, without changing any content:

```
agents/judge.json        the Judge
agents/settings.json     database address, reply style
agents/llms/<name>.json  one per LLM (budget, compliance, database, formatting, past_grants,
                         requirements, submission, writing) plus _TEMPLATE.json to copy
agents/README.md         how to add, remove or change an LLM
```

- `judge/judge.py`: `load_agents()` reads the folder. Files starting with `_` are ignored. A typo
  in a file gives an error naming that file and line.
- `compose.yaml` mounts the `agents/` folder, so edits apply without a rebuild. `judge/Dockerfile`
  copies it. `run_local.ps1`, `README.md` and `START_HERE.md` point to the new place.
- Tested: the folder loads exactly the same settings as the old `agents.json`. There's a new test
  (add, remove, typo), 19 tests pass, and a live Claude question was answered correctly.
- To undo: `git revert` this commit (it brings back `agents.json`).

## Step 6: merged with Medha's signed-in app (team main, Nov 15)

Medha added a web app with sign-in (`backend/`) and changed the dashboard to use it. Both changed
the same 4 files, so they were merged by hand:

- **One app, one container.** Her backend is now the server. The chat was added to it in the new
  file `backend/assistant.py` (`POST /api/chat`, `GET /api/team`). **Both need a signed-in user**
  (they return 401 otherwise), and each user has their own chat memory.
- `dashboard/index.html`: her v3 page (sign-in, live collaboration workspace, upload drop zones,
  panel-size slider), plus our real chat, short formatting, Grant teams cards and SAMPLE-004/005.
- `Dockerfile` (root): one image with her app plus the Judge (mongosh, Claude Code 2.1.319, agents/).
  It runs as a normal user, not root. `compose.yaml`: one service `app`, project name
  `grant-assistant`, port `APP_PORT` (default 8000) on 127.0.0.1 only, plus the Claude token and
  `MONGO_DB` from `.env`. `.env.example` has both her settings and ours.
- Removed `dashboard/Dockerfile`, `dashboard/nginx.conf` and `judge/Dockerfile` (replaced by the one image).
- `run_local.ps1` starts her app (with the chat) on http://localhost:8080.
- **Bugs found in her code and fixed:**
  - `backend/collaboration.py` imported `files.main` (her local folder name), so the app couldn't
    start from the repo. It now uses the package it's in.
  - `backend/config.py` looked for the demo user list one folder too high, so no one could sign in
    unless the path was set by hand. It now looks in `backend/`, as its own comment says. The
    default website folder is now `dashboard/`.
  - Her `Dockerfile` copied a `files/` folder that doesn't exist. Replaced (see above).
  - The chat sent the panel-size slider's value instead of the typed message. Fixed.
  - `.dockerignore` didn't exclude `.env`, so the Claude token could end up inside the image. Added.
- Tested locally: 19 tests pass. Chat and team are refused without sign-in (401). After demo
  sign-in, her collaboration workspace loads (1 document), Grant teams shows 6 people, and the chat
  answered "Is SAMPLE-003 within the budget limit?" correctly via the budget specialist.
- Not tested yet: the Docker build (no Docker on the PC).

## Running it on the VM

Run these in your own folder (`~/Team44`), never in a teammate's folder.

1. **Stop the old version first** (it used the name colin-judge and port 8090):
   `cd ~/Team44/ai-grant-project && docker compose -p colin-judge down`
2. Copy the zip from the PC (in **PowerShell on the PC**):
   `scp ai-grant-judge-code.zip cjw22003@uchcgrantai.cse.uconn.edu:~/Team44/`
3. On the VM: `cd ~/Team44 && unzip -o ai-grant-judge-code.zip && cd ai-grant-project`
4. Add the new settings to your existing `.env` (it keeps your Claude token):
   `printf 'APP_PORT=8090\nPUBLIC_URL=http://localhost:9090\n' >> .env`
5. `docker compose up -d --build`
6. From the PC: `ssh -N -L 9090:127.0.0.1:8090 cjw22003@uchcgrantai.cse.uconn.edu`, then open
   http://localhost:9090, choose a sign-in card, and pick a demo person.

**To undo on the VM:** `docker compose down` (removes only the grant-assistant container), then
delete `~/Team44/ai-grant-project`.
## Step 7: "who has or hasn't submitted their CV?" (simple version)

The bigger Monday-demo build (Docket tab, uploads, outbox emails) was undone, as asked. What's added:

- `samples/affiliation_sample1.csv`: the grant team (the professor's 5 investigators).
- `samples/cvs/`: the professor's CV files (`Data_Samples/CVs`). A CV counts as submitted when its
  file name starts with the person's last name. To mark someone as submitted, add their file here.
- `judge/judge.py`: new `cv_status` tool. Agent files can now list tools by name (`"tools": [...]`),
  and an unknown tool name gives a clear error.
- New LLM file `agents/llms/cv_checker.json`, plus one line in `agents/judge.json` that sends CV
  questions straight to it.
- `Dockerfile` copies the whole `samples/` folder.
- Tested: 20 tests pass. Live answer: "3 of 5 submitted (Liu, Slaughter, Creech); missing Tsai and Thomasen".
- To undo: `git revert` this commit.

## Step 8: budget spreadsheets from a plain request (Budget Builder)

- `agents/prompts/budget_spreadsheet.json`: the prompt. It holds the rules (NIH salary cap, calendar
  months, NRSA stipends, 27.5% fringe, $25,000 modules, 66.5% F&A), the exact JSON the AI must
  reply with, the sheet layout, the PHS 398 form mapping, the checks, and a worked example.
- New `judge/budget_sheet.py`: request -> AI JSON -> `check()` -> Excel with real formulas, saved in
  `data/budgets/`. The AI's totals are only used to catch mistakes, and impossible inputs are refused.
  Command line: `python judge/budget_sheet.py "..."` or `--from-json file.json` (no AI).
- New LLM `agents/llms/budget_builder.json` with the `make_budget_spreadsheet` tool.
- `backend/assistant.py`: `GET /api/budgets/<file>` to download, signed-in users only (401 otherwise).
- `dashboard/index.html`: chat replies can show download links, but only to this app's own `/api/` pages.
- `judge/requirements.txt`: openpyxl 3.1.5.
- Tested: 21 tests pass. Formulas were checked with a formula engine against Python math (example
  R21 = $166,500 a year; a 5-year test with cap, level 7 and equipment matched). Browser: "5-year
  R01, PI 2 months of $150k, postdoc level 3, $15k supplies" gave $1,248,750, checked by hand, and
  the downloaded file calculates the same.
- To undo: `git revert` this commit.
