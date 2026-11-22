"""Browser test setup: starts the real app (uvicorn) on a throwaway database and drives Chrome.

Run:   pytest tests/e2e
Needs: pip install playwright, plus a Chrome/Chromium. Uses `playwright install chromium` if present,
       otherwise set CHROME_PATH (for example /usr/bin/google-chrome).
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

sync_api = pytest.importorskip("playwright.sync_api")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """A fresh app server for each test, so tests cannot affect each other."""
    port = _free_port()
    env = {**os.environ, "AUTH_PROVIDER": "demo", "ALLOW_DEMO": "1",
           "DB_PATH": str(tmp_path / "app.db"), "STORAGE_DIR": str(tmp_path / "uploads"),
           "USERS_CSV": str(ROOT / "backend" / "allowed_users.demo.csv"),
           "FRONTEND_DIR": str(ROOT / "dashboard"), "PUBLIC_URL": f"http://127.0.0.1:{port}"}
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError((tmp_path / "server.log").read_text())
    yield base
    proc.terminate()
    proc.wait(5)
    log.close()


def api_client(base, login_id, entrance="pi"):
    """Sign in over HTTP, for setting up test data quickly."""
    c = httpx.Client(base_url=base)
    c.get(f"/auth/login?as={entrance}")
    c.get(f"/auth/demo/login?login_id={login_id}")
    assert c.get("/api/me").status_code == 200
    return c


def seed_pdf(base, login_id="pi.demo1", application_id="SAMPLE-001", name="Specific Aims"):
    c = api_client(base, login_id)
    r = c.post("/api/collaboration/documents",
               data={"application_id": application_id, "name": name, "category": "Research plan"},
               files={"file": ("aims.pdf", PDF_BYTES, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        path = os.environ.get("CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")
        try:
            b = p.chromium.launch()
        except Exception:
            if not path:
                pytest.skip("No browser: run `playwright install chromium` or set CHROME_PATH")
            b = p.chromium.launch(executable_path=path)
        yield b
        b.close()


class Watched:
    """A page plus everything that went wrong on it: script errors, console errors, failed requests."""

    def __init__(self, page):
        self.page = page
        self.errors = []
        self.sockets = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "favicon" not in (m.location or {}).get("url", "")
                and self.errors.append(f"console: {m.text}"))
        page.on("websocket", lambda ws: self.sockets.append(ws))

    def open_sockets(self):
        return [ws for ws in self.sockets if not ws.is_closed()]


@pytest.fixture
def open_as(browser, server):
    """open_as('pi.demo1') -> Watched page signed in through the real sign-in pages."""
    contexts = []

    def _open(login_id, entrance="pi"):
        ctx = browser.new_context()
        contexts.append(ctx)
        w = Watched(ctx.new_page())
        w.page.goto(server + "/")
        w.page.get_by_role("link", name="Sign in as SPS staff" if entrance == "staff"
                           else "Sign in as an investigator").click()
        w.page.locator(f'a[href*="login_id={login_id}"]').click()
        w.page.wait_for_load_state("networkidle")
        # The sign-in page's own check of /api/me returns 401 for a signed-out visitor by design;
        # only errors that happen once the user is signed in count.
        w.errors.clear()
        return w

    yield _open
    for ctx in contexts:
        ctx.close()
