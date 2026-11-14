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
