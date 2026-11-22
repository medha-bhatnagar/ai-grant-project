# Automated tests

Everything runs against throwaway databases and fictional demo users. Nothing touches real data.

## Setup (once)

    python -m venv .venv-test && . .venv-test/bin/activate
    pip install -r backend/requirements.txt -r tests/requirements-dev.txt
    playwright install chromium        # or set CHROME_PATH=/path/to/chrome
    npm install --no-save axe-core     # optional: accessibility checks

## Run

    pytest tests --ignore=tests/e2e    # backend only: about 3 seconds
    pytest tests/e2e                   # browser tests: starts the app and drives Chrome, about 1 minute
    pytest                             # both

## What is covered

- `test_auth_and_roles.py`: sign-in, sign-out, wrong entrance, each role only sees what it should.
- `test_documents.py`: upload, versions, download, PDF preview, file type and size checks.
- `test_collaboration.py`: collaborator and guest permissions, comments, tasks, chat over HTTP and WebSocket.
- `e2e/test_pages.py`: the real pages in Chrome: no script errors, one chat connection per page,
  PDF review dialog, uploads, deadlines, live chat between two people, SPS approval, the guest
  invitation flow, and axe-core accessibility scans (serious and critical problems fail the test).
