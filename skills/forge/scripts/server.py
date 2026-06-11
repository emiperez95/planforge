#!/usr/bin/env python3
"""planforge plan server.

Serves a single self-contained ``plan.html`` to the user's browser, waits for
the user to submit their edits, writes the submission to a response file, and
exits.

This process is spawned by the ``plan`` skill via Claude Code's Bash tool with
``run_in_background=true``. When it exits — after the user clicks *Send* or
*Approve* in the browser — the background bash terminates, the harness notifies
the agent, and the agent reads the response file to continue the loop.

stdlib only; no external dependencies. Python 3.10+.

CLI::

    python3 server.py \\
        --plan     /tmp/planforge-<run_id>/plan.html \\
        --response /tmp/planforge-<run_id>/response.json \\
        --run-id   <run_id>

The server binds 127.0.0.1 on an auto-selected free port and opens the browser
itself, so the caller never needs to know the port.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path


class PlanServer(socketserver.TCPServer):
    """A single-threaded TCP server that carries the run's file paths."""

    allow_reuse_address = True

    def __init__(self, server_address, handler_cls, plan_path: Path, response_path: Path):
        super().__init__(server_address, handler_cls)
        self.plan_path = plan_path
        self.response_path = response_path


class Handler(http.server.BaseHTTPRequestHandler):
    """Serve the plan on ``GET /`` and accept the submission on ``POST /submit``."""

    server: PlanServer  # type: ignore[assignment]  # narrowed for clarity

    def do_GET(self) -> None:
        if self._route() == "/":
            self._serve_plan()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self._route() != "/submit":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            # Persist something rather than losing the user's submission.
            payload = {"_parse_error": True, "raw": raw.decode("utf-8", "replace")}

        _write_json_atomic(self.server.response_path, payload)

        self.send_response(204)
        self.end_headers()

        # Shut the server down from a *separate* thread. Calling shutdown() from
        # within the request handler — which runs in serve_forever()'s own
        # thread — would deadlock (shutdown waits for the serve loop to notice
        # the stop flag, but the serve loop is busy running this handler).
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _serve_plan(self) -> None:
        try:
            body = self.server.plan_path.read_bytes()
        except OSError as exc:
            self.send_error(500, f"cannot read plan file: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> str:
        """Path without query string, e.g. ``/submit`` from ``/submit?x=1``."""
        return self.path.split("?", 1)[0]

    def log_message(self, *args) -> None:  # noqa: D401 - silence default logging
        pass


def _bind_server(port: int, plan_path: Path, response_path: Path) -> PlanServer:
    """Bind the server, retrying a few times when a specific port is requested.

    Across turns the skill reuses one port per run so the browser tab can poll
    the same URL and auto-advance. The previous turn's server has normally
    exited well before the next binds, but a short retry covers the handoff
    window if the OS is still releasing the socket. Port 0 (auto-select) never
    collides, so it is not retried.
    """
    attempts = 1 if port == 0 else 6
    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            return PlanServer(("127.0.0.1", port), Handler, plan_path, response_path)
        except OSError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(0.3)
    assert last_exc is not None
    raise last_exc


def _new_run_id() -> str:
    """A sortable, collision-resistant run id: ``<local timestamp>-<random>``.

    Format ``YYYYmmdd-HHMMSS-xxxxxxxx`` (e.g. ``20260608-143005-a1b2c3d4``). The
    timestamp prefix makes ``runs/`` list chronologically; the 8-hex random
    suffix rules out collisions within the same second. Generated in code so it
    has real entropy — minted **once** at turn 1 (via ``--new-run-id``) and then
    reused across every turn, so it must NOT be regenerated when the per-turn
    server restarts.
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _default_run_root() -> Path:
    """Default location for run artifacts: ``<repo>/runs``.

    ``server.py`` lives at ``<repo>/skills/forge/scripts/server.py``, so the
    repo root is ``parents[3]``. ``resolve()`` first so this is correct even when
    the skill is invoked through the personal-skill symlink
    (``~/.claude/skills/planforge -> <repo>/skills/forge``) — it canonicalizes to
    the real file in the repo. ``runs/`` is gitignored, so artifacts persist on
    disk across reboots without ever being committed to the public repo.
    """
    return Path(__file__).resolve().parents[3] / "runs"


def _write_json_atomic(path: Path, data: object) -> None:
    """Write ``data`` as pretty JSON, atomically (write temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="planforge plan server")
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="path to plan.html to serve (default: <run-dir>/plan.html)",
    )
    parser.add_argument(
        "--response",
        type=Path,
        default=None,
        help="path to write response.json (default: <run-dir>/response.json)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run id (required unless --new-run-id); names the run dir",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=None,
        help="root dir for run artifacts (default: <repo>/runs, gitignored)",
    )
    parser.add_argument(
        "--new-run-id",
        action="store_true",
        help="mint a fresh sortable run id, print it, and exit (call once at turn 1)",
    )
    parser.add_argument(
        "--print-run-dir",
        action="store_true",
        help="print the absolute run dir for --run-id and exit (no server)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="port to bind (default 0 = auto-select; reuse one port per run "
        "across turns so the browser can auto-advance)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="bind and serve but do not open a browser (for headless / testing)",
    )
    args = parser.parse_args(argv)

    # Mint-and-exit: generate the run id once at turn 1. Separate from server
    # startup on purpose — the server restarts every turn, and the id must stay
    # constant across them, so generation can't live in the serve path.
    if args.new_run_id:
        print(_new_run_id(), flush=True)
        return 0

    if not args.run_id:
        parser.error("--run-id is required (or use --new-run-id to mint one)")

    run_root = args.run_root or _default_run_root()
    run_dir = run_root / f"planforge-{args.run_id}"

    # Emit the run dir and exit — lets the agent learn the symlink-proof absolute
    # path before it writes plan.html (it can't reliably derive the repo root
    # from the skill's injected base dir when installed via symlink).
    if args.print_run_dir:
        print(run_dir, flush=True)
        return 0

    plan_path = args.plan or (run_dir / "plan.html")
    response_path = args.response or (run_dir / "response.json")

    try:
        if not plan_path.exists():
            sys.stderr.write(f"planforge: plan file not found: {plan_path}\n")
            return 2

        # Clear any stale response from a previous turn so a crash can't leave
        # the agent reading old data.
        if response_path.exists():
            response_path.unlink()

        with _bind_server(args.port, plan_path, response_path) as httpd:
            port = httpd.server_address[1]
            url = f"http://127.0.0.1:{port}/"
            print(f"planforge: serving plan on {url} (run {args.run_id})", flush=True)

            if args.no_browser:
                print(f"planforge: --no-browser set; open this URL manually: {url}", flush=True)
            else:
                opened = False
                try:
                    opened = webbrowser.open(url)
                except Exception as exc:  # webbrowser can raise on headless hosts
                    print(f"planforge: webbrowser.open failed: {exc}", flush=True)
                if not opened:
                    print(
                        f"planforge: could not auto-open a browser — open this URL manually: {url}",
                        flush=True,
                    )

            httpd.serve_forever()

        print("planforge: submission received, server exiting", flush=True)
        return 0

    except Exception:
        tb = traceback.format_exc()
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "server.err").write_text(tb, encoding="utf-8")
        except OSError:
            pass
        sys.stderr.write(tb)
        return 1


if __name__ == "__main__":
    sys.exit(main())
