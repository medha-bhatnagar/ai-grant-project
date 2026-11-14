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

## Running it on the VM (when ready)

Run these in your own folder (`~/Team44`), never in a teammate's folder.

1. Copy the zip from the PC (in **PowerShell on the PC**):
   `scp ai-grant-judge-code.zip cjw22003@uchcgrantai.cse.uconn.edu:~/Team44/`
2. On the VM: `cd ~/Team44 && unzip ai-grant-judge-code.zip && cd ai-grant-project`
3. Check that the ports are free: `ss -ltn | grep -E ':8090|:8010'` (no output means free).
4. `cp .env.example .env`, then make a Claude token **on the PC** with `claude setup-token` and
   paste it into `.env` with `nano .env`.
5. `docker compose up -d --build`, then `docker compose exec judge python judge.py setup-demo`.
6. From the PC: `ssh -L 8090:localhost:8090 cjw22003@uchcgrantai.cse.uconn.edu`, then open
   http://localhost:8090.

**To undo on the VM:** `docker compose down` (removes only the colin-judge containers), then
`mongosh grants --eval "db.dropDatabase()"` (removes only our `grants` database), then delete
`~/Team44/ai-grant-project`.
