# planforge — development plan

This document captures the development roadmap for planforge. It is the **handoff artifact** for any agent (or human) picking up work after Phase 1 (the initial scaffold). Phase 1 is complete; phases 2–4 are the work ahead.

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
skills/plan/
└── SKILL.md
```

(No `.gitkeep` files needed — `scripts/` and `assets/` will be populated in 2b and 2c.)

**SKILL.md frontmatter:**

```yaml
---
name: plan
description: Use when the user wants to collaboratively draft or refine a software plan that would benefit from diagrams and iterative editing before being locked in. Skip for plain-text outlines, simple lists, or one-shot planning where a single written response is enough.
disable-model-invocation: false
---
```

Notes on each field:
- `name: plan` — invoked as `/planforge:plan` (the plugin name from `plugin.json` provides the namespace).
- `description` — a routing signal for autonomous invocation. Tells Claude *when* to fire the skill, not *what* it does. The "Skip for…" clause prevents false positives on lightweight planning requests.
- `disable-model-invocation: false` — lets Claude autonomously invoke when the description matches. Confirmed against the [skills docs](https://code.claude.com/docs/en/skills.md): this field controls discovery/autonomous-loading only, not re-execution within an active session.
- `allowed-tools` — intentionally omitted. Skill inherits the agent's full tool set. Tighten in a later phase once Phase 2 reveals exactly what's needed.

**SKILL.md body (interim — replaced in Phase 2d):**

```markdown
# plan (WIP)

This skill is part of **planforge** — a closed-loop human-AI coplanner for
software plans that mix prose and diagrams. The skill is **not yet functional
in this version** (between Phase 2a and Phase 2d).

If invoked, do not attempt to run the loop. Instead, inform the user that
`/planforge:plan` is not yet functional and point them at `plan.md` in the
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

### Phase 2c — `assets/template.html` + `client.js` + `styles.css`

**Tech:** vanilla HTML + Mermaid 10 (LTS) + minimal CSS. No framework. No build step.

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

**client.js responsibilities:**

1. On load: read initial state from a `<script type="application/json" id="initial-state">…</script>` tag embedded in the HTML by the agent.
2. Render each Mermaid block in its container via `mermaid.render()`.
3. On every keystroke in any editor: re-render that Mermaid block live (debounced ~250 ms).
4. On "Send to agent" click:
   - Collect `full_state` from the editors.
   - POST `{ run_id, turn, action: "iterate", full_state, timestamp, user_notes }` to `/submit`.
   - Show "sent, waiting for new plan…" overlay. (Page replacement on the next turn is open question #2.)
5. On "Approve & finalize" click: same POST shape but `action: "approve"`. Show "approved" confirmation.

**For v0.1.0 the editor is a plain `<textarea>` per section.** CodeMirror / Monaco / drag-drop topology editing is deferred to v0.2.0+.

**Diagram-DSL editing only.** Visual node-edge manipulation is a research thread of its own; v0.1.0 captures the loop, not the editing UX.

**Commit:**

```
feat(plan): add HTML template with Mermaid render

Adds template.html + client.js + styles.css. Sections and diagrams
editable via textareas; Mermaid blocks re-render on keystroke (debounced).
Send and Approve buttons POST full_state to the local server.

Mermaid 10 (LTS) loaded from CDN for v0.1.0; vendoring locally is a
v0.2.0 concern.

Refs: plan.md Phase 2c.
```

**Effort:** ~1.5 days.

### Phase 2d — `SKILL.md` body

This is the most important file in the plugin: it tells the agent exactly how to run the loop. Replaces the interim WIP body from Phase 2a.

**Frontmatter:** unchanged from Phase 2a.

**Body structure** (Markdown, imperative voice — written for the agent, not the user):

````markdown
# plan — instructions

You are running an iterative human-AI coplanner loop. Follow these steps each time
the user invokes `/planforge:plan` or you decide to use this skill.

## When to invoke

Invoke when the user asks to draft a plan with diagrams, when they want to refine
an existing plan visually, or when an upstream skill hands off a draft for human review.

## Per-iteration workflow

### Step 1 — Generate the plan HTML

Read `${CLAUDE_PLUGIN_ROOT}/skills/plan/assets/template.html`. Substitute the
placeholder block `<script type="application/json" id="initial-state">…</script>`
with the current plan state: text sections and Mermaid diagrams.

If this is turn 1, generate from the user's seed prompt and create a fresh
run_id (UUID v4). On later turns, generate from the previous full_state +
the user_notes the user submitted, reusing the same run_id.

Write the result to `/tmp/planforge-{run_id}/plan.html`. Create the directory
if needed.

### Step 2 — Spawn the server (background)

Use Bash with `run_in_background=true`:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/plan/scripts/server.py \
    --plan /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --run-id {run_id}
```

This call returns immediately. The server opens the user's browser, serves
the plan, and will exit when the user clicks Send or Approve. You do not
need to manage the browser or the port — the server handles both.

### Step 3 — End your turn

Tell the user briefly: "Plan opened in your browser. Edit and click Send when
ready. You can keep chatting with me here in parallel; I'll pick up your edits
as soon as you send them." Then end the turn.

Do not poll. Do not `BashOutput` to check progress. The user can chat with
you while editing (each user message gives you a new turn); answer those
normally without referencing the background process. When the user submits
in the browser, you will receive a `<task-notification>` indicating the
background bash completed. That notification triggers Step 4.

### Step 4 — On notification, read the response

When the harness fires the `<task-notification>` for this run's background
bash, read `/tmp/planforge-{run_id}/response.json`.

### Step 5 — Process the response

If `action == "approve"`:
  - Save the final HTML to a per-run `converged.html` in the case study directory
    (or `/tmp/planforge-{run_id}/converged.html` if no case study path is set).
  - Inform the user: "Plan approved. Final converged HTML saved to <path>."
  - Exit the loop.

If `action == "iterate"`:
  - Read `full_state` and `user_notes` from the response.
  - Compose a refined plan HTML incorporating the user's edits.
  - If `user_notes` is non-empty, treat it as additional NL guidance from the user.
  - Loop back to Step 1.

## Logging

After every iteration (including the final approve), write:

- `experiments/{run}/iterations/turn-{NN}_plan.html` — the HTML the agent sent.
- `experiments/{run}/iterations/turn-{NN}_response.json` — the user's response.

The `{run}` directory is the experiment folder set by the user upstream
(e.g., `case-studies/url-shortener/experiments/2026-05-26_v1.0_planning_sonnet-46/`).
If unset, default to `/tmp/planforge-{run_id}/iterations/`.

## Failure modes

- **Browser open failed.** If `webbrowser.open()` fails (headless / SSH session),
  the server logs the URL to stdout. The agent should read the background-bash
  output file once after spawning the server to surface this URL to the user
  as a fallback.
- **Server error.** Check for `/tmp/planforge-{run_id}/server.err`; surface
  contents to the user if present.
- **Orphan server.** If the user closes the browser without submitting, the
  server runs indefinitely (v0.1.0 has no idle timer). The agent is waiting
  on a notification that may never fire. If the user explicitly asks to
  cancel, kill the background bash via its shell ID. A configurable
  server-side idle timeout is a v0.2.0+ refinement.
````

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

| Sub-phase | Effort |
|---|---|
| 2a — Skill scaffold | ~10 min |
| 2b — server.py | ~0.5 day |
| 2c — HTML + JS + CSS | ~1.5 days |
| 2d — SKILL.md body | ~0.5 day |
| 2e — README + CHANGELOG + release | ~0.5 day |

**Decision points (decide before or during the relevant sub-phase):**
- **Mermaid version (2c):** v10 LTS (recommended) vs v11 latest. Lock in early to avoid breaking the template across iterations.
- **Mermaid loading (2c):** CDN (`unpkg.com/mermaid@10/dist/mermaid.min.js`) for v0.1.0; vendor locally is a v0.2.0+ concern.
- **Browser auto-refresh between turns (2c):** open question #2 below — needs an answer before 2c's client.js is written.
- **Logging path resolution (2d):** open question #1 below.

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

1. **Run logging path resolution (Phase 2d).** SKILL.md proposes `experiments/{run}/iterations/` but how does the skill know `{run}`? Either (a) the user/caller passes it as an arg in the seed prompt, or (b) the skill defaults to `/tmp/planforge-{run_id}/iterations/` and the user copies after. Decide before v0.1.0.
2. **Browser auto-refresh on next turn (Phase 2c).** When the agent generates a new plan after iteration, how does the user know to refresh? Either (a) the skill instructs the user to refresh, (b) the server pushes via SSE / WebSocket (more work), or (c) the new server URL differs each turn so the browser navigates fresh. Decide before v0.1.0.
3. **Mermaid syntax error handling (Phase 2c).** If the user types invalid Mermaid in a diagram block, the live render fails. Should the Send button be disabled, or should the bad block be sent anyway with a warning?
4. **Multiple concurrent runs.** Per-run UUID handles isolation, but is there a UI / list-runs command? Defer to v0.2.0+ unless v0.1.0 testing reveals friction.
5. **Orphan server protection.** v0.1.0 has no server-side idle timer; if the user closes the browser without submitting, the server runs forever and the agent waits on a notification that never fires. Add a configurable idle timeout (default ~30 min) in v0.2.0+. For v0.1.0, document the manual-cancel workaround in SKILL.md.

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
