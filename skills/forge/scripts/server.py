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

Alongside the plan loop it maintains two run-metadata files in the run dir —
``manifest.json`` (run identity + per-turn boundaries and token usage) and
``session.log`` (an append-only event timeline). Both are additive: every write
is best-effort and can never take down the serve path. Token usage is filled in
after the fact by ``--reconcile-usage``; see ``_collect_usage`` for why it is
reconstructed rather than self-reported.
"""

from __future__ import annotations

import argparse
import glob
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# Token components, kept separate on purpose. They are priced differently
# (cache read ~0.1x base, cache write ~1.25x base), so a single summed "input"
# figure cannot be converted back into a cost. No price is ever recorded here:
# a list-price calculation is not a charge, and on subscription auth nothing is
# billed per call.
TOKEN_FIELDS = ("input_uncached", "input_cache_write", "input_cache_read", "output")


class PlanServer(socketserver.TCPServer):
    """A single-threaded TCP server that carries the run's file paths."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        handler_cls,
        plan_path: Path,
        response_path: Path,
        idle_timeout: float = 0.0,
    ):
        super().__init__(server_address, handler_cls)
        self.plan_path = plan_path
        self.response_path = response_path
        self.idle_timeout = idle_timeout
        self.timed_out = False
        self._last_activity = time.monotonic()
        self._activity_lock = threading.Lock()

    def touch(self) -> None:
        """Record inbound activity; resets the idle countdown."""
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        with self._activity_lock:
            return time.monotonic() - self._last_activity


class Handler(http.server.BaseHTTPRequestHandler):
    """Serve the plan on ``GET /`` and accept the submission on ``POST /submit``."""

    server: PlanServer  # type: ignore[assignment]  # narrowed for clarity

    def do_GET(self) -> None:
        self.server.touch()
        route = self._route()
        if route == "/":
            self._serve_plan()
        elif route == "/ping":
            # Keepalive from an open plan page. The page does NOT poll while the
            # user is editing (cross-turn polling only starts after a submit),
            # so without this beat an idle timer could not tell "user is typing"
            # from "user closed the tab". Answering 204 is enough — touch()
            # above already reset the countdown.
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        self.server.touch()
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


def _bind_server(
    host: str,
    port: int,
    plan_path: Path,
    response_path: Path,
    idle_timeout: float = 0.0,
) -> PlanServer:
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
            return PlanServer((host, port), Handler, plan_path, response_path, idle_timeout)
        except OSError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(0.3)
    assert last_exc is not None
    raise last_exc


def _start_idle_watchdog(httpd: PlanServer, run_id: str) -> None:
    """Stop the server after ``idle_timeout`` seconds with no inbound request.

    Guards the orphan-server failure mode: if the user closes the tab without
    submitting, nothing ever hits ``/submit``, the process would run forever and
    the agent would wait on a notification that never fires. On expiry we write a
    ``timeout`` response so the background task completes normally and the agent
    is woken with something it can act on.

    Disabled unless a positive timeout is passed — see ``--idle-timeout``.
    """
    if httpd.idle_timeout <= 0:
        return

    def watch() -> None:
        tick = max(1.0, min(5.0, httpd.idle_timeout / 10))
        while True:
            time.sleep(tick)
            if httpd.idle_seconds() < httpd.idle_timeout:
                continue
            httpd.timed_out = True
            _safe(
                _write_json_atomic,
                httpd.response_path,
                {
                    "run_id": run_id,
                    "action": "timeout",
                    "error": "timeout",
                    "idle_timeout_s": httpd.idle_timeout,
                    "timestamp": _iso_now(),
                },
            )
            httpd.shutdown()
            return

    threading.Thread(target=watch, daemon=True).start()


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


def _iso_now() -> str:
    """Current UTC time, ISO 8601 with a trailing Z (matches transcript stamps)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe(fn, *a, **kw):
    """Run a best-effort side task; never let it break the serve path.

    Every manifest / session-log / discussion-log write goes through here. Losing
    a metadata line is an acceptable outcome; losing the user's plan because a
    metadata write raised is not.
    """
    try:
        return fn(*a, **kw)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Run metadata: manifest.json + session.log
# --------------------------------------------------------------------------


def _skill_dir() -> Path:
    """The skill root (``.../skills/forge``) — this file's grandparent."""
    return Path(__file__).resolve().parents[1]


def _planforge_version() -> str | None:
    """Version from ``.claude-plugin/plugin.json``, if this is a repo checkout."""
    try:
        manifest = _skill_dir().parents[1] / ".claude-plugin" / "plugin.json"
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except Exception:
        return None


def _planforge_commit() -> str | None:
    """Current HEAD of the planforge checkout, if git is available."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_skill_dir(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _session_id() -> str | None:
    """The Claude Code session this run belongs to.

    Claude Code exports ``CLAUDE_CODE_SESSION_ID`` into the Bash environment, so
    the skill can identify its own session without a hook. Captured once at run
    start and stored in the manifest, because ``--reconcile-usage`` may run later
    from a different process where the variable is absent.
    """
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _manifest_path(run_dir: Path) -> Path:
    return run_dir / "manifest.json"


def _read_manifest(run_dir: Path) -> dict:
    try:
        return json.loads(_manifest_path(run_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _init_manifest(run_dir: Path, run_id: str) -> dict:
    """Create manifest.json if absent, stamping run start and environment.

    Called from ``--print-run-dir``, which the skill runs at turn 1 *before* it
    generates any plan HTML. That ordering matters: ``run_start_ts`` becomes the
    lower bound of turn 1's generation window, so usage reconstruction does not
    have to sweep in unrelated session activity from before the run began.
    """
    manifest = _read_manifest(run_dir)
    if manifest.get("run_id"):
        return manifest
    manifest = {
        "run_id": run_id,
        "run_start_ts": _iso_now(),
        "planforge_version": _planforge_version(),
        "planforge_commit": _planforge_commit(),
        "python_version": sys.version.split()[0],
        "skill_dir": str(_skill_dir()),
        "claude_session_id": _session_id(),
        "turns": [],
    }
    _write_json_atomic(_manifest_path(run_dir), manifest)
    return manifest


def _manifest_turn_entry(manifest: dict, turn: int) -> dict:
    for entry in manifest.setdefault("turns", []):
        if entry.get("turn") == turn:
            return entry
    entry = {"turn": turn}
    manifest["turns"].append(entry)
    manifest["turns"].sort(key=lambda e: e.get("turn") or 0)
    return entry


def _resolve_turn(manifest: dict, explicit: int | None) -> int:
    """Turn number for this server start.

    Prefers ``--turn`` when the skill passes it. Older SKILL.md revisions do not,
    so fall back to "one past the last turn that already recorded a submission",
    which reconstructs the sequence correctly for the normal one-server-per-turn
    lifecycle.
    """
    if explicit is not None:
        return explicit
    turns = manifest.get("turns") or []
    if not turns:
        return 1
    done = [t for t in turns if t.get("submit_ts")]
    if len(done) == len(turns):
        return len(turns) + 1
    return max((t.get("turn") or 0) for t in turns)


def _record_bind(run_dir: Path, run_id: str, turn: int, port: int, host: str) -> None:
    manifest = _read_manifest(run_dir) or _init_manifest(run_dir, run_id)
    manifest.setdefault("run_id", run_id)
    if not manifest.get("claude_session_id"):
        manifest["claude_session_id"] = _session_id()
    entry = _manifest_turn_entry(manifest, turn)
    entry["bind_ts"] = _iso_now()
    entry["port"] = port
    entry["host"] = host
    manifest["turn_count"] = len(manifest.get("turns") or [])
    _write_json_atomic(_manifest_path(run_dir), manifest)
    _append_session_log(run_dir, f"turn={turn} port={port} bound")


def _record_submission(run_dir: Path, run_id: str, turn: int, response: dict) -> None:
    action = response.get("action") or "unknown"
    manifest = _read_manifest(run_dir) or _init_manifest(run_dir, run_id)
    entry = _manifest_turn_entry(manifest, turn)
    entry["submit_ts"] = _iso_now()
    entry["action"] = action
    manifest["turn_count"] = len(manifest.get("turns") or [])
    manifest["run_end_ts"] = entry["submit_ts"]
    manifest["final_action"] = action
    _write_json_atomic(_manifest_path(run_dir), manifest)
    _append_session_log(run_dir, f"turn={turn} submission action={action}")


def _append_session_log(run_dir: Path, message: str) -> None:
    """Append one event line to ``session.log``.

    Wall-clock per turn is then computable without inferring from filesystem
    mtimes, which are fragile across reboots and clock changes.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "session.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{_iso_now()} {message}\n")


# --------------------------------------------------------------------------
# Durable discussion log
# --------------------------------------------------------------------------


def _extract_initial_state(plan_path: Path) -> dict | None:
    """Pull the embedded plan state out of a served ``plan.html``.

    The page carries its state in a single ``<script id="initial-state">`` JSON
    block, so the served HTML is self-describing — the discussion log can be
    rebuilt from run artifacts alone with no extra contract between agent and
    server.
    """
    try:
        html = plan_path.read_text(encoding="utf-8")
    except OSError:
        return None
    marker = 'id="initial-state"'
    idx = html.find(marker)
    if idx == -1:
        return None
    start = html.find(">", idx)
    end = html.find("</script>", start)
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(html[start + 1 : end])
    except json.JSONDecodeError:
        return None


def _append_discussion(log_path: Path, turn: int, plan_state: dict | None, response: dict) -> None:
    """Append this turn's exchange to a durable, human-readable planning record.

    Captures the *discussion*, not just the outcome: what the agent proposed,
    what the user changed it to, and what they said about it. Opt-in via
    ``--discussion-log`` so a run can be studied or cited after the fact without
    digging through per-turn HTML.
    """
    proposed_text = {}
    proposed_diagrams = {}
    title = None
    if plan_state:
        title = (plan_state.get("meta") or {}).get("title")
        proposed_text = {i.get("id"): i.get("value", "") for i in plan_state.get("text") or []}
        proposed_diagrams = {
            i.get("id"): i.get("value", "") for i in plan_state.get("diagrams") or []
        }

    full = response.get("full_state") or {}
    final_text = full.get("text") or {}
    final_diagrams = full.get("diagrams") or {}

    edited = sorted(
        [k for k, v in final_text.items() if proposed_text.get(k, v) != v]
        + [k for k, v in final_diagrams.items() if proposed_diagrams.get(k, v) != v]
    )

    lines = [
        f"\n## Turn {turn} — {response.get('action') or 'unknown'}",
        "",
        f"- time: {_iso_now()}",
    ]
    if title:
        lines.append(f"- plan: {title}")
    lines.append(f"- sections edited by user: {', '.join(edited) if edited else 'none'}")

    notes = (response.get("user_notes") or "").strip()
    lines += ["", "### User notes", "", notes if notes else "_(none)_"]

    if edited:
        lines += ["", "### User edits"]
        for key in edited:
            before = proposed_text.get(key, proposed_diagrams.get(key, ""))
            after = final_text.get(key, final_diagrams.get(key, ""))
            fence = "mermaid" if key in final_diagrams else "text"
            lines += [
                "",
                f"**{key}** — agent proposed:",
                "",
                f"```{fence}",
                str(before).strip(),
                "```",
                "",
                f"**{key}** — user submitted:",
                "",
                f"```{fence}",
                str(after).strip(),
                "```",
            ]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        header = f"# planforge planning discussion\n\nRun log — every turn's proposal and the user's response.\n"
        log_path.write_text(header, encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# Token usage reconciliation (post-hoc, from the Claude Code transcript)
# --------------------------------------------------------------------------


def find_transcript(session_id: str) -> Path | None:
    """Locate a Claude Code session transcript by session id.

    Globs ``~/.claude/projects/*/<session-id>.jsonl`` rather than deriving the
    directory from cwd: that encoding maps BOTH ``/`` and ``.`` to ``-``, so it
    is lossy and cannot be reconstructed unambiguously. The session id is unique,
    so the glob is exact.
    """
    pattern = os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl")
    matches = sorted(glob.glob(pattern))
    return Path(matches[0]) if matches else None


def _collect_usage(transcript: Path) -> list[dict]:
    """Per-message token usage from a session transcript, deduplicated.

    **Dedup by ``message.id`` is mandatory.** The transcript writes one JSONL
    line per *content block*, and repeats the whole-message ``usage`` object
    verbatim on every one of them — a reply with one text block and four tool
    calls appears as five lines all reporting the same totals. Summing lines
    therefore multiplies usage by the block count (measured 2.0x-3.8x on real
    sessions). Keeping the first line per message id both deduplicates and
    yields the earliest timestamp, which is what turn attribution wants.
    """
    seen: set[str] = set()
    events: list[dict] = []
    with transcript.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            message = obj.get("message") or {}
            mid = message.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            usage = message.get("usage") or {}
            events.append(
                {
                    "ts": obj.get("timestamp"),
                    "model": message.get("model"),
                    "sidechain": bool(obj.get("isSidechain")),
                    "input_uncached": usage.get("input_tokens") or 0,
                    "input_cache_write": usage.get("cache_creation_input_tokens") or 0,
                    "input_cache_read": usage.get("cache_read_input_tokens") or 0,
                    "output": usage.get("output_tokens") or 0,
                }
            )
    return events


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _zero_tokens() -> dict:
    return {k: 0 for k in TOKEN_FIELDS}


def _add_tokens(target: dict, event: dict) -> None:
    for key in TOKEN_FIELDS:
        target[key] += event.get(key, 0)


def reconcile_usage(run_dir: Path) -> tuple[int, str]:
    """Fill per-turn token usage into manifest.json from the session transcript.

    Why post-hoc rather than self-reported: a message's usage does not exist
    until that message completes, so an agent can never report its own. The
    server, meanwhile, restarts every turn and its bind/submit stamps bracket the
    work exactly. So we record precise boundaries live and attribute usage by
    time window afterwards.

    Windows per turn N, contiguous and non-overlapping so nothing is counted
    twice across the restart:

    * ``generation``    ``(submit_ts(N-1) or run_start, bind_ts(N)]`` — the agent
      building this turn's plan.
    * ``parallel_chat`` ``(bind_ts(N), submit_ts(N)]`` — anything said in the
      session while the user was editing in the browser.

    Known limits, recorded in the manifest rather than papered over: the
    transcript has no run tagging, so unrelated conversation inside a window is
    counted too, and the run's final assistant message (emitted after the last
    tool call) is not yet flushed when this runs.
    """
    manifest = _read_manifest(run_dir)
    if not manifest:
        return 1, "no manifest.json in run dir"

    session_id = manifest.get("claude_session_id")
    usage_meta: dict = {
        "method": "transcript-timewindow",
        "reconciled_ts": _iso_now(),
        "dedup": "by message.id (transcript repeats usage per content block)",
        "caveats": [],
    }

    def bail(reason: str, code: int = 1) -> tuple[int, str]:
        usage_meta["status"] = "unavailable"
        usage_meta["reason"] = reason
        manifest["usage"] = usage_meta
        _safe(_write_json_atomic, _manifest_path(run_dir), manifest)
        return code, reason

    if not session_id:
        return bail("CLAUDE_CODE_SESSION_ID was not set when the run started")

    transcript = find_transcript(session_id)
    if not transcript:
        return bail(f"no transcript found for session {session_id}")

    events = _collect_usage(transcript)
    turns = sorted(manifest.get("turns") or [], key=lambda t: t.get("turn") or 0)
    if not turns:
        return bail("manifest records no turns")

    # Build the (lower, upper] windows described above.
    windows: list[tuple[dict, str, datetime | None, datetime | None]] = []
    prev_submit = _parse_ts(manifest.get("run_start_ts"))
    for entry in turns:
        bind = _parse_ts(entry.get("bind_ts"))
        submit = _parse_ts(entry.get("submit_ts"))
        windows.append((entry, "generation", prev_submit, bind))
        if bind:
            windows.append((entry, "parallel_chat", bind, submit))
        prev_submit = submit or bind or prev_submit

    for entry in turns:
        entry["tokens"] = {
            "generation": _zero_tokens(),
            "parallel_chat": _zero_tokens(),
        }

    totals = _zero_tokens()
    sidechain = _zero_tokens()
    sidechain_seen = False
    unattributed = _zero_tokens()
    models: set[str] = set()

    for event in events:
        ts = _parse_ts(event.get("ts"))
        if ts is None:
            continue
        placed = False
        for entry, bucket, lower, upper in windows:
            if lower is not None and ts <= lower:
                continue
            if upper is not None and ts > upper:
                continue
            if upper is None and lower is None:
                continue
            _add_tokens(entry["tokens"][bucket], event)
            placed = True
            break
        if not placed:
            _add_tokens(unattributed, event)
            continue
        _add_tokens(totals, event)
        if event.get("model"):
            models.add(event["model"])
        if event.get("sidechain"):
            sidechain_seen = True
            _add_tokens(sidechain, event)

    # One model is the normal case; a list only if the session switched models.
    # Left absent entirely when nothing was attributed, rather than an empty list
    # that reads like "no model was used".
    if len(models) == 1:
        manifest["model"] = next(iter(models))
    elif models:
        manifest["model"] = sorted(models)
    else:
        manifest.pop("model", None)
    usage_meta.update(
        {
            "status": "ok",
            "transcript": str(transcript),
            "messages_counted": len(events),
            "totals": totals,
            "unattributed": unattributed,
        }
    )
    if sidechain_seen:
        usage_meta["sidechain_subset"] = sidechain
        usage_meta["caveats"].append(
            "sidechain (subagent) messages are included in totals and mirrored in "
            "sidechain_subset; this path is unverified — no sidechain entries were "
            "available to test against."
        )
    usage_meta["caveats"] += [
        "attribution is by time window; unrelated conversation in the same "
        "session during a window is counted in it — the transcript has no run tag.",
        "the run's final assistant message is not yet flushed when reconciliation "
        "runs from within that same message, so it is not counted.",
    ]

    overlap = _detect_concurrent_runs(run_dir, session_id, manifest)
    if overlap:
        usage_meta["status"] = "unattributable"
        usage_meta["caveats"].append(
            "another run in this session overlaps in time (" + ", ".join(overlap) + "); "
            "windows cannot be separated, so these figures are not trustworthy."
        )

    manifest["usage"] = usage_meta
    _write_json_atomic(_manifest_path(run_dir), manifest)
    return 0, f"reconciled {len(events)} messages across {len(turns)} turns"


def _detect_concurrent_runs(run_dir: Path, session_id: str, manifest: dict) -> list[str]:
    """Sibling runs from the same session whose time span overlaps this one.

    Concurrent runs make the time windows ambiguous. Flag rather than guess.
    """
    start = _parse_ts(manifest.get("run_start_ts"))
    end = _parse_ts(manifest.get("run_end_ts")) or datetime.now(timezone.utc)
    if not start:
        return []
    clashes = []
    for sibling in sorted(run_dir.parent.glob("planforge-*")):
        if sibling == run_dir or not sibling.is_dir():
            continue
        other = _read_manifest(sibling)
        if other.get("claude_session_id") != session_id:
            continue
        o_start = _parse_ts(other.get("run_start_ts"))
        o_end = _parse_ts(other.get("run_end_ts")) or datetime.now(timezone.utc)
        if o_start and o_start <= end and start <= o_end:
            clashes.append(sibling.name)
    return clashes


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
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host/interface to bind (default 127.0.0.1). WARNING: 0.0.0.0 "
        "exposes the plan on the local network, and there is no authentication "
        "— anyone who can reach the port can read the plan and submit a response "
        "that the agent will act on. Use only on a trusted network.",
    )
    parser.add_argument(
        "--turn",
        type=int,
        default=None,
        help="turn number this server is serving (recorded in manifest.json / "
        "session.log; inferred from the manifest when omitted)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=0.0,
        help="seconds with no inbound request before giving up and writing a "
        "timeout response (default 0 = disabled). Needs a plan page that sends "
        "the /ping keepalive, otherwise a long edit looks idle.",
    )
    parser.add_argument(
        "--discussion-log",
        type=Path,
        default=None,
        help="append a durable human-readable planning record (agent proposal, "
        "user edits, user notes) to this markdown file after each submission",
    )
    parser.add_argument(
        "--reconcile-usage",
        action="store_true",
        help="fill per-turn token usage into manifest.json from the Claude Code "
        "session transcript, then exit (no server)",
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

    # Reconcile-and-exit: post-hoc pass, never part of the serve path.
    if args.reconcile_usage:
        code, message = reconcile_usage(run_dir)
        stream = sys.stdout if code == 0 else sys.stderr
        stream.write(f"planforge: {message}\n")
        return code

    # Emit the run dir and exit — lets the agent learn the symlink-proof absolute
    # path before it writes plan.html (it can't reliably derive the repo root
    # from the skill's injected base dir when installed via symlink). Also stamps
    # the manifest, fixing the run's start time before any generation happens.
    if args.print_run_dir:
        print(run_dir, flush=True)
        _safe(_init_manifest, run_dir, args.run_id)
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

        manifest = _safe(_read_manifest, run_dir) or {}
        turn = _resolve_turn(manifest, args.turn)

        with _bind_server(
            args.host, args.port, plan_path, response_path, args.idle_timeout
        ) as httpd:
            port = httpd.server_address[1]
            display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
            url = f"http://{display_host}:{port}/"
            print(f"planforge: serving plan on {url} (run {args.run_id})", flush=True)

            _safe(_record_bind, run_dir, args.run_id, turn, port, args.host)
            _start_idle_watchdog(httpd, args.run_id)

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
            timed_out = httpd.timed_out

        if timed_out:
            print(
                f"planforge: idle timeout after {args.idle_timeout:g}s with no activity — "
                "wrote a timeout response and exited",
                flush=True,
            )
            _safe(_append_session_log, run_dir, f"turn={turn} timeout idle={args.idle_timeout:g}s")
            return 0

        response = _safe(lambda: json.loads(response_path.read_text(encoding="utf-8"))) or {}
        _safe(_record_submission, run_dir, args.run_id, turn, response)
        if args.discussion_log:
            _safe(
                _append_discussion,
                args.discussion_log,
                turn,
                _extract_initial_state(plan_path),
                response,
            )

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
