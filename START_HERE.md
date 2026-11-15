# START HERE: run the AI Grant Assistant on your own Windows PC

About 10 minutes the first time. After that it's only step 4.

## 1. Install these (once)

- **Python 3.12** from https://www.python.org/downloads/ (when you install, tick **"Add python.exe to PATH"**)
- **MongoDB and mongosh**: open PowerShell and run

      winget install MongoDB.Server MongoDB.Shell

- **Claude Code** (the AI). In PowerShell:

      irm https://claude.ai/install.ps1 | iex
      claude auth login

  Sign in with your Claude account (Pro or higher).

Close PowerShell and open a new one so it picks up the new programs.

## 2. Unzip this folder

Put it anywhere, for example `Documents\ai-grant-project`.

## 3. Load the sample data (once)

In PowerShell, inside the folder:

    powershell -ExecutionPolicy Bypass -File setup_demo.ps1

This puts made-up grants (SAMPLE-001 to SAMPLE-005) and the good and bad sample grant files
into a database on **your own PC** only.

## 4. Start it

    powershell -ExecutionPolicy Bypass -File run_local.ps1

Open **http://localhost:8080**, click **Continue to UConn sign-in** on the Investigator card, and
pick **Demo PI One** (demo sign-in, no password). Then use the Assistant chat. Press **Ctrl+C** in
PowerShell to stop.

## Try saying

- "Hi" (it walks you through starting a new application and building the team)
- "Review SAMPLE-005"
- "Summarize SAMPLE-004 in 5 sentences"
- "Is SAMPLE-003 within the budget limit?"

## Where things are

- `agents/`: the Judge and one file per LLM (see `agents/README.md` to add, remove or change one)
- `people.json`: the people the assistant can add to a grant team
- `dashboard/index.html`: the website
- `backend/`: the web app (sign-in, requests, collaboration)
- `judge/`: the code that runs it (no need to edit)
- `README.md`: more detail
