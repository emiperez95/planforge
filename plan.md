# planforge — development plan

This document captures the development roadmap for planforge. It is the **handoff artifact** for any agent (or human) picking up work. Phases 1 and 2 are complete — **v0.1.0 shipped 2026-05-31**, the loop runs end-to-end. The remaining phases (3–4) are upstream work in the separate thesis-research repo, not this one.

The plan is opinionated about *what* and *why*, but conservative on *how*: implementation details (exact lines of code, exact library versions beyond the constraints below) are left to the implementer.

---

## Project context

planforge is a [Claude Code](https://claude.com/claude-code) plugin that implements a **closed-loop human-AI coplanner** for software planning artifacts that mix prose and diagrams.

The loop:

1. The agent (Claude Code session) generates an HTML page containing a draft plan: prose sections + Mermaid diagrams, and writes it to disk.
2. The plugin's `plan` skill launches a local HTTP server via a `Bash` tool call with `run_in_background=true` — the call returns immediately. The server binds an auto-selected port, opens the user's browser itself, and serves the HTML. The agent ends its turn.
3. The user edits prose and diagrams in the browser. They can also keep chatting with the agent in Claude Code in parallel. When they click **Send to agent**, the browser POSTs the updated state to the local server.
4. The server writes the response to a known file and exits cleanly. The background bash terminates → the harness fires a `<task-notification>` → the agent wakes up in a new turn with the response in hand.
5. The agent reads the response, refines the plan, generates a new HTML, and re-invokes the loop. It continues until the user clicks **Approve & finalize**.

**Why this matters.** Most diagram-as-spec workflows force the user into either pure-text iteration or out-of-band tooling. planforge closes the loop with rendered, editable diagrams and automatic round-trip. It was designed originally to support empirical research on diagram-driven AI workflows (see [Upstream context](#upstream-context)), but the pattern generalizes to any human-AI co-creation task.

**Auth model.** The plugin reuses the agent's existing Claude Code session — there are no API keys to manage and no external services to call. Anyone with Claude Code installed and the plugin available can run the loop.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Claude Code session (the agent)                                │
│                                                                 │
│   1. Generates plan HTML, writes to disk                        │
│                       │                                         │
│                       ▼                                         │
│   2. Bash (run_in_background=true): python server.py …          │
│      (returns immediately; agent ends turn)                     │
│                       │                                         │
│                       ▼                                         │
│   (agent is idle; user can chat in parallel while editing)      │
│                       │                                         │
│                       ▼                                         │
│   3. <task-notification> fires → agent wakes in new turn        │
│      Reads /tmp/planforge-{run_id}/response.json                │
│      If action="iterate": refine plan, loop to step 1           │
│      If action="approve": save converged.html, exit             │
└─────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ (server exits → bg bash terminates
                                  │  → harness notifies agent)
                                  │
┌─────────────────────────────────────────────────────────────────┐
│  Local Python server (subprocess of the background Bash)        │
│   • stdlib http.server only — no external deps                  │
│   • binds an auto-selected free port                            │
│   • subprocess-opens the browser (open / xdg-open / start)      │
│   • GET / → serves plan.html                                    │
│   • POST /submit → writes response.json, sys.exit(0)            │
│   • lifetime: until user submits (no Bash timeout cap in bg)    │
└─────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ HTTP POST (full state of edited plan)
                                  │
┌─────────────────────────────────────────────────────────────────┐
│  Browser (opened by the server)                                 │
│   • Renders prose + Mermaid diagrams from plan.html             │
│   • Per-section textareas for prose, per-diagram textareas for  │
│     Mermaid DSL (with live render)                              │
│   • "Send to agent" → POSTs full state, action="iterate"        │
│   • "Approve & finalize" → POSTs full state, action="approve"   │
└─────────────────────────────────────────────────────────────────┘
```

**Key design properties:**

- **Background bash + completion notification as wakeup.** The agent spawns the server via `Bash` with `run_in_background=true`, then ends its turn. When the user submits, the server exits, the background bash terminates, and Claude Code's harness fires a `<task-notification>` that resumes the agent in a new turn. No polling.
- **Parallel chat.** Because the agent isn't blocked on a tool call, the user can keep chatting with Claude Code while editing in the browser. Both channels feed the agent.
- **No API keys.** Plugin lives inside Claude Code; agent uses its existing session.
- **No external dependencies.** Python stdlib only. The server is a single file.
- **Per-run isolation.** Each run has a UUID; tmp paths are scoped under it.
- **Server opens browser itself.** Cross-platform browser launching is handled in one place (Python's `webbrowser` module), not duplicated as shell snippets in SKILL.md.
- **No editing-time cap.** Background bash has no timeout; the user can take as long as they want. A server-side idle timer (e.g. 30 min) is a v0.2.0+ refinement to protect against orphaned processes.
- **Immutable iteration artifacts.** Once a run completes, its `iterations/turn-NN_*` artifacts are immutable. New edits → new run folder.

---

## Versioning

planforge uses semver. The `version` field in `plugin.json` bumps as part of the release commit at the end of each phase — never speculatively.

Planned tags:

| Tag    | Meaning                                            | When                                   |
|--------|----------------------------------------------------|----------------------------------------|
| v0.0.0 | Initial scaffold; no functionality                 | Phase 1 (retroactive on commit `468e252`) |
| v0.1.0 | First functional release; loop works end-to-end    | End of Phase 2                         |

References to "v0.2.0+" in later sections indicate deferred features beyond the first functional release.

---

## Phase 2 — Functional v0.1.0

**Goal:** first release that runs end-to-end. Loop converges on a trivial example. Plugin is installable from GitHub release.

Phase 2 has five sub-phases (2a → 2e). They build sequentially: each needs the previous before it can be exercised end-to-end. Sub-phase boundaries are also commit boundaries.

> **Naming note.** The skill was originally named `plan` and renamed to **`forge`**
> (invoked `/planforge:forge`) to avoid colliding with Claude Code's native plan
> mode. The commit-message blockquotes below are kept verbatim and use the
> historical `feat(plan):` scope (those commits are already in git history);
> future skill commits use the `forge` scope.

### Pre-step: retroactive v0.0.0 tag

Before any Phase 2 commits, retroactively tag the existing initial scaffold commit as v0.0.0 so the version history is well-formed:

```bash
git tag -a v0.0.0 468e252 -m "v0.0.0 — initial scaffold"
git push origin v0.0.0
```

(Adjust the SHA if the scaffold commit has moved.)

### Phase 2a — Skill structure + frontmatter

**Goal:** the skill directory exists with valid frontmatter. The skill is now discoverable by Claude Code's plugin tooling, but is **not yet functional** — invoking it does nothing useful.

**Files to create:**

```
skills/forge/
└── SKILL.md
```

(No `.gitkeep` files needed — `scripts/` and `assets/` will be populated in 2b and 2c.)

**SKILL.md frontmatter:**

```yaml
---
name: forge
description: Use when the user wants to collaboratively draft or refine a software plan that would benefit from diagrams and iterative editing before being locked in. Skip for plain-text outlines, simple lists, or one-shot planning where a single written response is enough.
disable-model-invocation: false
---
```

Notes on each field:
- `name: forge` — invoked as `/planforge:forge` (the plugin name from `plugin.json` provides the namespace).
- `description` — a routing signal for autonomous invocation. Tells Claude *when* to fire the skill, not *what* it does. The "Skip for…" clause prevents false positives on lightweight planning requests.
- `disable-model-invocation: false` — lets Claude autonomously invoke when the description matches. Confirmed against the [skills docs](https://code.claude.com/docs/en/skills.md): this field controls discovery/autonomous-loading only, not re-execution within an active session.
- `allowed-tools` — intentionally omitted. Skill inherits the agent's full tool set. Tighten in a later phase once Phase 2 reveals exactly what's needed.

**SKILL.md body (interim — replaced in Phase 2d):**

```markdown
# forge (WIP)

This skill is part of **planforge** — a closed-loop human-AI coplanner for
software plans that mix prose and diagrams. The skill is **not yet functional
in this version** (between Phase 2a and Phase 2d).

If invoked, do not attempt to run the loop. Instead, inform the user that
`/planforge:forge` is not yet functional and point them at `plan.md` in the
repo root for status and roadmap.
```

**Commit:**

```
feat(plan): scaffold skill directory with frontmatter

Adds skills/plan/SKILL.md with frontmatter and a WIP body. Skill is
now discoverable by Claude Code's plugin tooling but is non-functional;
body instructs the agent to refuse invocation until Phase 2d lands.

Refs: plan.md Phase 2a.
```

**Effort:** ~10 minutes (plus 1 minute for the retroactive v0.0.0 tag).

### Phase 2b — `server.py`

**Tech:** Python 3.10+ stdlib only (`http.server`, `socketserver`, `json`, `argparse`, `pathlib`, `subprocess`, `webbrowser`, `signal`).

**CLI shape:**

```bash
python server.py \
    --plan /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --run-id <uuid>
```

The server binds an auto-selected free port. No `--port` flag — the agent doesn't need to know the port, since the server opens the browser itself.

**Endpoints:**

- `GET /` — serve `plan.html`.
- `POST /submit` — accept `Content-Type: application/json`, write payload to `response.json`, return `204 No Content`, then `sys.exit(0)` once the response is flushed.

**Lifecycle:**

- On startup: bind port → `webbrowser.open(url)` to launch the browser → `serve_forever()`.
- On POST `/submit`: write `response.json`, return 204, then call `server.shutdown()` from a background thread so the current response finishes flushing before the serve loop exits.
- On uncaught exception: write `/tmp/planforge-{run_id}/server.err` with traceback, exit non-zero.
- No internal idle timer in v0.1.0. The process lives until the user submits. Adding a configurable idle timeout (default ~30 min) to guard against orphaned servers — where the user closes the browser without submitting — is a v0.2.0+ refinement.

**POST payload schema (v0.1.0 — simplified):**

```json
{
  "run_id": "uuid-v4",
  "turn": 3,
  "action": "iterate" | "approve",
  "full_state": {
    "text":     {"<section_id>": "..."},
    "diagrams": {"<diagram_id>": "mermaid DSL string"}
  },
  "timestamp": "2026-05-26T10:23:45Z",
  "user_notes": "free-form note user typed before clicking send (optional)"
}
```

For v0.1.0 we ship `full_state` only — no client-side semantic delta computation. The agent diffs against its own prior state when needed. Stable per-diagram IDs across edits, structural deltas, and semantic alignment are v0.2.0+ refinements.

**Commit:**

```
feat(plan): add stdlib HTTP server with submit-and-exit lifecycle

Implements server.py: auto-port bind, server-side browser open,
GET / serves plan.html, POST /submit writes response.json and
shuts down cleanly via a background-thread server.shutdown() call.
No PID files, no idle timer. Designed to be spawned via Claude
Code's Bash tool with run_in_background=true; the background bash
terminates when the server exits, triggering a task-notification
that resumes the agent.

Refs: plan.md Phase 2b.
```

**Effort:** ~0.5 day (simpler than the original background-bash design — saves the lifecycle code).

### Phase 2c — `assets/template.html` (self-contained) + `server.py --port`

**Tech:** one self-contained HTML file — inline CSS plus a single ESM
`<script type="module">` that imports Mermaid **11.15.0** from jsdelivr. No
framework, no build step, no separate `client.js` / `styles.css` (kept inline so
the agent writes exactly one file to `/tmp`). The agent only swaps the
`#initial-state` JSON block; the page builds its editors from that data.

**Layout:**

```
┌─────────────────────────────────────────────────────────┐
│  Header: plan title + turn N + run_id (short hash)      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Text sections (prose, editable via textarea per section)│
│                                                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Diagrams (each diagram: title + textarea for Mermaid   │
│  DSL + live render below)                               │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  Optional notes textarea                                │
├─────────────────────────────────────────────────────────┤
│  [ Send to agent ]   [ Approve & finalize ]             │
└─────────────────────────────────────────────────────────┘
```

**Page-script responsibilities:**

1. On load: read initial state via `getElementById('initial-state').textContent` (a `<script type="application/json" id="initial-state">` block the agent fills in). Build a labeled textarea per text section and per diagram.
2. Render each Mermaid block via `mermaid.render()`; show parse errors inline in that diagram's preview (Send stays enabled — raw DSL goes through anyway). Clean up any orphan nodes a failed render leaves behind.
3. On every keystroke in a diagram editor: re-render that block live (debounced ~250 ms).
4. On "Send to agent" click:
   - Collect `full_state` (`{text:{id:value}, diagrams:{id:value}}`) from the editors.
   - POST `{ run_id, turn, action: "iterate", full_state, timestamp, user_notes }` to `/submit`.
   - Show a "waiting for the next version…" overlay, then **auto-advance** (see below).
5. On "Approve & finalize" click: same POST shape but `action: "approve"`. Show an "approved" confirmation; no polling (the loop ends).

**Cross-turn auto-advance (resolves open question #2).** The skill reuses one
port per run (`server.py --port`). After an iterate-send the page polls `GET /`
on the same origin; the just-exited server is gone, so polls fail until the
agent starts the next turn's server on the same port. When a server answers
with a **different** turn number, the page reloads into the new plan. The turn
comparison (parsed via `DOMParser` + `getElementById`, not regex) guards the
brief window where the old server might still answer. The user never re-opens a
URL — the tab advances on its own.

**For v0.1.0 the editor is a plain `<textarea>` per section.** CodeMirror / Monaco / drag-drop topology editing is deferred to v0.2.0+.

**Diagram-DSL editing only.** Visual node-edge manipulation is a research thread of its own; v0.1.0 captures the loop, not the editing UX.

**Commit:**

```
feat(plan): add self-contained plan template with Mermaid render and auto-advance

Single self-contained template.html (inline CSS + ESM module JS, Mermaid
11.15.0 from jsdelivr). Prose + diagram textareas; diagrams re-render live
on keystroke with inline error recovery. Send/Approve POST full_state.

Cross-turn auto-advance: server.py gains --port (reuse one port per run,
with bind-retry over the handoff window) and the page polls + reloads when
a new turn appears on that port.

Refs: plan.md Phase 2c.
```

Mermaid 11.15.0 from CDN for v0.1.0; vendoring locally is a v0.2.0 concern.

**Effort:** ~1.5 days. **Status: done** — verified in a real browser (Mermaid
render, live edit + error recovery with no orphan nodes, exact `full_state`
round-trip, unattended turn 1 → turn 2 auto-advance).

### Phase 2d — `SKILL.md` body

The body is the brain of the plugin: imperative instructions telling the agent
how to drive the loop. The full text is in `skills/forge/SKILL.md` (the source of
truth); this section records the as-built shape and the decisions behind it.

**Per-iteration flow encoded in SKILL.md:**

1. **Generate** `/tmp/planforge-{run_id}/plan.html` from the template, replacing
   only the `#initial-state` JSON (`{meta, text[], diagrams[]}`) with this turn's
   state. Keep `id`s and `run_id` stable; set `turn` so the browser detects the
   new version.
2. **Spawn the server** with `run_in_background=true`.
   - *Turn 1:* `--port 0` (auto); the browser opens. Read the background task's
     output **once** to capture the bound port; remember it for the run.
   - *Turn N>1:* `--port {port} --no-browser` — the open tab is polling that port
     and reloads itself, so no new tab is spawned.
3. **End the turn** with a short message. No polling; the user can chat in
   parallel. A `<task-notification>` for the server's task resumes the agent.
4. **Read** `/tmp/planforge-{run_id}/response.json`.
5. **Process:** `iterate` → apply `full_state` + `user_notes`, increment turn,
   loop; `approve` → regenerate `converged.html`, summarize in chat, exit.

**Decisions baked in:**
- **Port handoff:** turn 1 auto-selects and the agent reads the port from the
  background task output once (not polling); later turns reuse it. This is what
  makes the browser auto-advance work.
- **One tab:** `--no-browser` on turns 2+ prevents a new tab each turn.
- **Logging path — resolves open question #1:** everything under
  `/tmp/planforge-{run_id}/` (per-turn `iterations/turn-NN_*` and
  `converged.html`). Zero config; copy out anything worth keeping. A durable,
  configurable output location — and a saved record of the planning discussion —
  is deferred (see Future work).

**Status: done.**

**Commit:**

```
feat(plan): add SKILL.md orchestration instructions for agent

Imperative-voice instructions covering the per-iteration workflow,
logging conventions, and failure modes. Skill now drives the closed
loop end-to-end with the server (2b) and HTML (2c) it ships with.

Refs: plan.md Phase 2d.
```

**Effort:** ~0.5 day.

### Phase 2e — README, CHANGELOG, release

**Status: done — v0.1.0 shipped 2026-05-31** (tagged `v0.1.0`, pushed). README
rewritten with `--plugin-dir` install + `/planforge:forge` usage; CHANGELOG
regenerated via `npx git-cliff` (git-cliff not installed globally); `plugin.json`
bumped to 0.1.0. The optional narrative summary (step 4) was skipped to keep the
CHANGELOG fully git-cliff-reproducible. A GitHub Release (step 7) is still
optional/pending.

1. Update `README.md` with real install + usage instructions.
2. Bump `plugin.json` version `0.0.0` → `0.1.0`.
3. Run `git cliff -o CHANGELOG.md --tag v0.1.0` to regenerate the changelog from commits.
4. Optionally add a 1–2 line narrative summary at the top of the new version section.
5. Commit:
   ```
   docs: release v0.1.0 — first functional loop

   Bumps plugin.json to 0.1.0, regenerates CHANGELOG.md via git-cliff
   covering all Phase 2 commits, updates README install/usage with
   concrete instructions.

   v0.1.0 is the first usable release: agent can drive the plan loop
   end-to-end with HTML editing in browser and full-state round-trip.

   Refs: plan.md Phase 2e.
   ```
6. Tag and push:
   ```
   git tag -a v0.1.0 -m "v0.1.0 — first functional loop"
   git push origin main --tags
   ```
7. Optionally create a GitHub release via `gh release create v0.1.0` for visibility.

**Effort:** ~0.5 day.

### Phase 2 total

**~3 working days** for one person.

| Sub-phase | Effort | Status |
|---|---|---|
| 2a — Skill scaffold | ~10 min | **done** |
| 2b — server.py | ~0.5 day | **done** |
| 2c — template.html + `--port` | ~1.5 days | **done** |
| 2d — SKILL.md body | ~0.5 day | **done** |
| 2e — README + CHANGELOG + release | ~0.5 day | **done** |

**Decision points:**
- **Mermaid version (2c):** ✅ resolved — pinned **11.15.0** (latest), loaded as the ESM build from jsdelivr. Vendoring locally is a v0.2.0+ concern.
- **Template structure (2c):** ✅ resolved — single self-contained file (inline CSS + module JS), not separate `client.js` / `styles.css`.
- **Browser auto-refresh between turns (2c):** ✅ resolved (open question #2) — pinned port per run + poll-and-reload on turn change.
- **Logging path (2d):** ✅ resolved (open question #1) — everything under `/tmp/planforge-{run_id}/`; no config. Durable output is deferred (see Future work).

---

## Phase 3 — Thesis-research integration (upstream)

This phase **does not happen in this repo.** It is done in the [thesis-research](https://github.com/emiperez95/thesis-research) repo (where this plugin was conceived) by a separate agent or session. Listed here for context only.

In thesis-research:

1. New entry in `docs/methodology-log.md` recording the split-into-separate-repo decision and pinning planforge v0.1.0 SHA.
2. Schema update in `case-studies/url-shortener/experiments/README.md` adding fields `plugin_repo`, `plugin_version`, `plugin_commit` to the `context.md` template.
3. Reference to planforge in `case-studies/url-shortener/README.md` under "Tools used".
4. Possible update to `CLAUDE.md` "Working conventions" noting tooling lives in a separate repo.

**The planforge agent should NOT modify thesis-research.** Cross-repo work is coordinated manually or by the user.

---

## Phase 4 — First empirical run (upstream)

Also **not in this repo.** First real experiment using planforge runs in thesis-research, in the URL-shortener case study, producing the first empirical data for the upstream thesis.

Listed here only so the planforge agent knows that `v0.1.0` will be exercised on a real case study and that bugs surfaced there should be ported back as `fix:` commits in planforge.

---

## Upstream context

planforge was carved out of [thesis-research](https://github.com/emiperez95/thesis-research) for distribution and citability. The upstream thesis investigates diagrams-as-spec for AI coding workflows; planforge is one of the empirical tools it produces.

If you are an agent picking up this plan, you do not need to read the thesis to build the plugin — `plan.md` and `SKILL.md` are self-contained. But if questions arise about *why* design decisions were made (e.g., "why ship full_state instead of structural deltas?"), the thesis repo's `docs/research/diagram-diff-representation.md` and `docs/methodology-log.md` are the canonical references.

---

## Open questions to resolve during execution

These are not blockers but should be revisited as Phase 2 lands:

1. ✅ **Run logging path resolution (Phase 2d) — RESOLVED.** Everything goes under `/tmp/planforge-{run_id}/` (per-turn `iterations/turn-NN_*` and `converged.html`). Zero config; the user copies out anything worth keeping. A durable, configurable output location is deferred to Future work.
2. ✅ **Browser auto-refresh on next turn (Phase 2c) — RESOLVED.** Pinned port per run (`server.py --port`) + the page polls `GET /` and reloads when a different turn number appears. No SSE, no manual refresh, no per-turn URL. Verified in-browser.
3. ✅ **Mermaid syntax error handling (Phase 2c) — RESOLVED.** Invalid DSL shows an inline error in that diagram's preview and Send stays enabled — the raw DSL is sent anyway so the agent can help fix it. Orphan nodes from a failed render are cleaned up. Verified in-browser.
4. **Multiple concurrent runs.** Per-run UUID handles isolation, but is there a UI / list-runs command? Defer to v0.2.0+ unless v0.1.0 testing reveals friction.
5. ✅ **Orphan server protection — RESOLVED.** `server.py --idle-timeout <s>` gives up after that long with no inbound request, writes an `action: "timeout"` response and exits, so the background task completes and the agent is woken instead of waiting forever. The plan page sends a `GET /ping` keepalive every 20s, so a tab that is merely open (however long the user takes) never trips the timer — only a closed one does. Verified in a real browser: with a 45s timeout, an open tab survived 65s and a closed one expired in ~43s. Ships **default-off** (`0` = disabled) so that a run already in flight against an older `SKILL.md` — which neither passes the flag nor knows the `timeout` response shape — cannot be affected; `SKILL.md` passes `1800`. Flip the default in a later release once no old sessions are plausibly live.

## Run metadata and token accounting (as-built)

Closes [issue #1](https://github.com/emiperez95/planforge/issues/1). A run left
behind enough to *reproduce* the loop but not to *measure* it. Three files now
fill that gap, all written by `server.py`, all additive — no existing artifact
renamed or restructured, and neither wire contract touched.

- **`manifest.json`** — run identity (id, planforge version + commit, python
  version, skill dir, Claude Code session id, start/end) and one entry per turn
  with `bind_ts` / `submit_ts` / `action` / `port`, plus token usage.
- **`session.log`** — append-only `bound` / `submission` / `timeout` timeline, so
  per-turn wall-clock never has to be inferred from filesystem mtimes.
- **`discussion.md`** — the durable planning record (see the deferred item this
  replaces): per turn, the agent's proposal, which sections the user edited,
  before/after for each, and their notes. Path set by `--discussion-log`, so it
  can live outside the run dir.

### Why usage is reconstructed, not self-reported

Investigated before implementing; three findings drove the design.

1. **The agent cannot report its own usage.** A message's token counts do not
   exist until that message completes, so self-reporting at write time is
   structurally impossible — not merely awkward. What *is* available is
   `CLAUDE_CODE_SESSION_ID`, exported into the Bash environment, so the skill can
   identify its own session with no hook. Transcripts are located by globbing
   `~/.claude/projects/*/<session-id>.jsonl` rather than encoding cwd, because
   that encoding maps both `/` and `.` to `-` and cannot be reversed.

2. **Summing `usage` per JSONL line over-counts badly.** The transcript writes one
   line per *content block* and repeats the whole-message `usage` verbatim on each
   — a reply with one text block and four tool calls appears five times with
   identical totals. Measured inflation: **2.0x–3.8x** on real sessions.
   Deduplicating by `message.id` is mandatory, and keeping the first line per id
   also yields the earliest timestamp, which is what attribution wants.

3. **The four token components must stay separate.** `input_tokens` is only the
   uncached remainder; the rest sits in `cache_creation_input_tokens` and
   `cache_read_input_tokens`, priced differently (~1.25x and ~0.1x base). A single
   summed input figure cannot be converted back into a cost. **No price is
   recorded** — a list-price calculation is not a charge, and on subscription auth
   nothing is billed per call. Tokens only; price stays derivable later.

### Turn attribution

The server restarts every turn, so its bind/submit stamps bracket the work
exactly. `--reconcile-usage` attributes messages to contiguous, non-overlapping
windows — nothing counted twice across the restart:

| bucket | window |
|---|---|
| `generation` | `(submit_ts(N-1) or run_start, bind_ts(N)]` — agent building the plan |
| `parallel_chat` | `(bind_ts(N), submit_ts(N)]` — conversation while the user edited |

`run_start_ts` is stamped by `--print-run-dir`, which runs at turn 1 *before* any
generation, giving turn 1 a tight lower bound instead of an open-ended one.

### Limits recorded rather than papered over

The manifest carries `usage.status` and `usage.caveats`; consumers must read them
before quoting a number.

- **Unrelated conversation is counted.** The transcript has no run tag, so
  anything said in-session inside a window lands in it. Unfixable, so it is stated.
- **Concurrent runs in one session** make windows ambiguous — detected by
  comparing sibling manifests and marked `unattributable` rather than guessed.
- **The final assistant message** of a run is not yet flushed when reconciliation
  runs from inside that same message, so it is not counted.
- **Sidechain (subagent) messages** are included and mirrored into a separate
  `sidechain_subset`, but this path is **unverified** — no sidechain entries
  existed on the machine to test against.
- **No session id** (variable unset) ⇒ `status: "unavailable"` with a reason, and
  no totals at all rather than fabricated zeros.

Deviation from the issue: it proposed a per-turn `iterations/turn-NN_meta.json`
written by the agent. Superseded by the manifest's per-turn entries — one file,
and it avoids asking the agent to write token fields it provably cannot know. The
authoritative `model` likewise comes from the transcript, not self-report.

## Future work (post-v0.1.0)

Deferred intentionally to keep v0.1.0 small. Not blockers; revisit as real use
surfaces the need.

- **Multi-run management.** A `list-runs` / status surface if concurrent runs
  cause friction (open question #4). Still open — note that concurrent runs in
  one Claude Code session also make token attribution ambiguous, which the
  manifest now detects and flags as `unattributable`.
- **Richer editing.** CodeMirror for prose/DSL, vendored Mermaid (no CDN),
  and — further out — visual node/edge diagram editing with structural deltas
  and stable diagram IDs across edits.

## Effort summary

| Phase | Effort | Cumulative |
|---|---|---|
| 1 — Scaffold | 20 min | 20 min (done) |
| 2a — Skill scaffold | ~10 min | 30 min |
| 2b — server.py | ~0.5 day | ~0.5 day |
| 2c — HTML + JS + CSS | ~1.5 days | ~2 days |
| 2d — SKILL.md body | ~0.5 day | ~2.5 days |
| 2e — README + CHANGELOG + release v0.1.0 | ~0.5 day | ~3 days |
| 3 — Thesis-research integration (upstream) | ~45 min | — |
| 4 — First empirical run (upstream) | ~2–3 hours | — |

Realistic single-developer timeline: **~3 working days to v0.1.0** with focused effort, longer with interruptions.
