# planforge — development plan

This document captures the development roadmap for planforge. It is the **handoff artifact** for any agent (or human) picking up work after Phase 1 (the initial scaffold). Phase 1 is complete; phases 2–5 are the work ahead.

The plan is opinionated about *what* and *why*, but conservative on *how*: implementation details (exact lines of code, exact library versions beyond the constraints below) are left to the implementer.

---

## Project context

planforge is a [Claude Code](https://claude.com/claude-code) plugin that implements a **closed-loop human-AI coplanner** for software planning artifacts that mix prose and diagrams.

The loop:

1. The agent (Claude Code session) generates an HTML page containing a draft plan: prose sections + Mermaid diagrams.
2. The plugin's `iterate-plan` skill launches a local HTTP server in the background (via `Bash run_in_background=true`) serving the HTML, and opens the browser detached so the user can edit.
3. The user edits prose and diagrams in the browser. When they click **Send to agent**, the browser POSTs a structured delta + full state to the local server.
4. The server writes the response to a known file and exits cleanly (`sys.exit(0)`). Because the background bash terminates, Claude Code automatically notifies the agent — no polling, no API key juggling, no blocking.
5. The agent reads the response, refines the plan, generates a new HTML, and re-invokes the skill. The loop continues until the user clicks **Approve & finalize**.

**Why this matters.** Most diagram-as-spec workflows force the user into either pure-text iteration or out-of-band tooling. planforge closes the loop with rendered, editable diagrams and automatic round-trip. It was designed originally to support empirical research on diagram-driven AI workflows (see [Upstream context](#upstream-context)), but the pattern generalizes to any human-AI co-creation task.

**Auth model.** The plugin reuses the agent's existing Claude Code session — there are no API keys to manage and no external services to call. Anyone with Claude Code installed and the plugin available can run the loop.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Claude Code session (the agent)                                │
│                                                                 │
│   1. Generates initial plan HTML                                │
│                       │                                         │
│                       ▼                                         │
│   2. Invokes Skill iterate-plan with the HTML                   │
│                       │                                         │
│                       ▼                                         │
│         Skill instructs agent to:                               │
│           • write plan to /tmp/planforge-{run_id}/plan.html     │
│           • Bash run_in_background=true: server.py …            │
│           • Bash: open the browser detached                     │
│           • return control (agent does other work / idles)      │
│                                                                 │
│                       …                                         │
│                                                                 │
│   N. Notification arrives: background bash completed            │
│   N+1. Agent reads /tmp/planforge-{run_id}/response.json        │
│   N+2. If action="iterate": refine plan, loop                   │
│         If action="approve": save converged.html, exit          │
└─────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ (server exits → bash bg completes
                                  │  → harness notifies agent)
                                  │
┌─────────────────────────────────────────────────────────────────┐
│  Local Python server (subprocess of Bash run_in_background)     │
│   • stdlib http.server (BaseHTTPRequestHandler)                 │
│   • GET / → serves plan.html with run_id injected               │
│   • POST /submit → writes response.json, sys.exit(0)            │
│   • Idle timeout 30 min → exit gracefully                       │
│   • Listens on auto-selected free port                          │
└─────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ HTTP POST (Layer A delta + full state)
                                  │
┌─────────────────────────────────────────────────────────────────┐
│  Browser (detached, user-controlled)                            │
│   • Renders prose + Mermaid diagrams from plan.html             │
│   • Provides edit affordances (textarea / DSL editor for v0.1)  │
│   • "Send to agent" button POSTs Layer A delta + full state     │
│   • "Approve & finalize" button POSTs action=approve            │
└─────────────────────────────────────────────────────────────────┘
```

**Key design properties:**

- **No polling.** Background bash + completion notification is the synchronization primitive.
- **No API keys.** Plugin lives inside Claude Code; agent uses its existing session.
- **Per-run isolation.** Each run has a UUID; tmp paths and tmp ports are scoped under it.
- **Inmutable inputs.** Once a run completes, its `iterations/turn-NN_*` artifacts are immutable. New edits → new run folder.
- **Browser detached.** User can keep typing in Claude Code session while editing in browser; both channels feed into the agent.

---

## Phase 2 — Skill skeleton (v0.0.0 tag)

**Goal:** the skill directory exists with `SKILL.md` frontmatter and asset placeholders. Skill is **not yet functional** but plugin-dev tooling recognizes it.

### Files to create

```
skills/iterate-plan/
├── SKILL.md                ← frontmatter + body marked WIP
├── scripts/
│   └── .gitkeep
├── assets/
│   └── .gitkeep
└── references/
    └── .gitkeep            ← for future internal docs grep'able by agent
```

### SKILL.md skeleton

Frontmatter (required):

```yaml
---
name: iterate-plan
description: Launch an HTML-based human-AI coplanner loop for spec + diagram iteration. Agent generates plan, user edits in browser, agent receives changes.
allowed-tools: [Bash, Read, Write]
disable-model-invocation: false
---
```

Body in Phase 2 is a 3–5 line placeholder noting that the skill is WIP and pointing at `plan.md`. Full instructions land in Phase 3c.

### Commit and tag

```
git commit -m "feat(iterate-plan): scaffold skill skeleton

Adds skills/iterate-plan/ with SKILL.md frontmatter and asset dirs.
Skill is non-functional — body marked WIP. Instructions for agent
loop (launch server, open browser, wait notification, read response,
iterate) will land in Phase 3.

Refs: planforge/plan.md Phase 2."
```

Then:

```
git tag -a v0.0.0 -m "v0.0.0 — scaffold complete, skill skeleton present"
git push origin main --tags
```

**Effort:** ~10 minutes.

**Decision points before committing:**
- Confirm `allowed-tools` list. `Bash` is required; `Read` and `Write` cover reading the response file and saving converged.html.
- Confirm `disable-model-invocation: false` — needed for agent to re-invoke the skill on each iteration without user re-typing `/iterate-plan`.

---

## Phase 3 — Functional v0.1.0

**Goal:** first release that runs end-to-end. Loop converges on a trivial example. Plugin is installable from GitHub release.

Phase 3 has four sub-phases. They are roughly parallel but `3a` (server) should land before `3b` (HTML) so the HTML has something to POST to.

### Phase 3a — `server.py`

**Tech:** Python 3.10+ stdlib only (`http.server`, `socketserver`, `json`, `uuid`, `argparse`, `pathlib`, `signal`).

**CLI shape:**

```bash
python server.py \
    --plan /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --port 0                                            # 0 = auto-select free port
    --idle-timeout 1800                                 # seconds, default 30 min
```

Print the resolved port to stdout on startup so the calling agent can read it and pass to `open <url>`.

**Endpoints:**

- `GET /` — serve `plan.html` content with `{{run_id}}` placeholder replaced.
- `POST /submit` — accept `Content-Type: application/json`, write payload to `response.json`, return `204 No Content`, schedule `sys.exit(0)` after the response is flushed.
- `GET /health` (optional) — `200 OK` for browser to detect server is alive.

**Lifecycle:**

- Write PID to `/tmp/planforge-{run_id}/server.pid` on startup.
- On `SIGTERM` / idle timeout / submit POST: cleanup PID file, exit 0.
- On exception: write `/tmp/planforge-{run_id}/server.err` with traceback, exit non-zero.

**Layer A delta schema** (the structure the browser POSTs):

```json
{
  "run_id": "uuid-v4",
  "turn": 3,
  "action": "iterate" | "approve",
  "delta": {
    "text": {
      "<section_id>": {"before": "…", "after": "…"}
    },
    "diagrams": {
      "<diagram_id>": {
        "added":    [{"id": "…", "label": "…", "type": "node|edge", "…": "…"}],
        "removed":  [{"id": "…"}],
        "modified": [{"id": "…", "field": "…", "before": "…", "after": "…"}]
      }
    }
  },
  "full_state": {
    "text":     {"<section_id>": "…"},
    "diagrams": {"<diagram_id>": "mermaid DSL string"}
  },
  "timestamp": "2026-05-26T10:23:45Z",
  "user_notes": "free-form note user typed before clicking send (optional)"
}
```

`full_state` is included so the agent has a complete snapshot per turn without needing to replay deltas.

**Commit:**

```
feat(iterate-plan): add stdlib HTTP server with submit-and-exit lifecycle

Implements server.py with auto-port selection, per-run UUID isolation,
PID file, idle timeout, and Layer A delta schema. POST /submit writes
response.json and triggers graceful exit so Claude Code's background-bash
completion notification fires.

Refs: plan.md Phase 3a.
```

### Phase 3b — `assets/template.html` + `client.js` + `styles.css`

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
   - Compute Layer A delta vs initial state.
   - POST `{ run_id, turn, action: "iterate", delta, full_state, timestamp, user_notes }` to `/submit`.
   - Show "sent, waiting for new plan…" overlay. (The page will be replaced when the agent's next iteration loads — handled by the agent re-opening the URL.)
5. On "Approve & finalize" click: same POST shape but `action: "approve"`. Show "approved" confirmation.

**For v0.1.0 the editor is a plain `<textarea>` per section.** CodeMirror / Monaco / drag-drop topology editing is deferred to v0.2.0+.

**Diagram-DSL editing only.** Visual node-edge manipulation is a research thread of its own; v0.1.0 captures the loop, not the editing UX.

**Commit:**

```
feat(iterate-plan): add HTML template with Mermaid render and Layer A delta extraction

Adds template.html + client.js + styles.css. Sections and diagrams editable
via textareas; Mermaid blocks re-render on keystroke (debounced). Send and
Approve buttons POST Layer A delta + full state to local server.

Mermaid 10 (LTS) loaded from CDN for v0.1.0; vendoring locally is a v0.2.0
concern.

Refs: plan.md Phase 3b.
```

### Phase 3c — `SKILL.md` content

This is the most important file in the plugin: it tells the agent exactly how to run the loop.

**Frontmatter:** as in Phase 2.

**Body structure** (Markdown, imperative voice — written for the agent, not the user):

```markdown
# iterate-plan — instructions

You are running an iterative human-AI coplanner loop. Follow these steps each time
the user invokes /iterate-plan or you decide to use this skill.

## When to invoke

Invoke when the user asks to draft a plan with diagrams, when they want to refine
an existing plan visually, or when an upstream skill hands off a draft for human review.

## Per-iteration workflow

### Step 1 — Generate the plan HTML

Read `${CLAUDE_PLUGIN_ROOT}/skills/iterate-plan/assets/template.html`. Substitute
the placeholder block `<script type="application/json" id="initial-state">…</script>`
with the current plan state: text sections and Mermaid diagrams.

If this is turn 1, generate from the user's seed prompt. If later turn, generate
from the previous full_state + the delta the user submitted.

Write the result to `/tmp/planforge-{run_id}/plan.html`. Create the directory if
needed; use a stable run_id for the entire session (generate at turn 1, reuse).

### Step 2 — Launch the server (background)

Use Bash with `run_in_background=true`:

```bash
python ${CLAUDE_PLUGIN_ROOT}/skills/iterate-plan/scripts/server.py \
    --plan /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --port 0 \
    --idle-timeout 1800
```

Capture the printed port from the server's stdout.

### Step 3 — Open the browser detached

Use Bash (sync, instant):

```bash
open http://localhost:{port}/          # macOS
xdg-open http://localhost:{port}/      # Linux
start http://localhost:{port}/         # Windows
```

Print the URL to the user as a fallback in case auto-open fails.

### Step 4 — Return control

After step 3, return a short message to the user: "Plan opened in browser. Edit
freely and click Send when ready. You can keep chatting with me here in parallel
— I'll pick up your edits automatically when you send them."

Then end your turn. Do not poll. Do not check on the server. You will be notified
automatically when the background bash completes (which happens when the server
exits after receiving a POST or timing out).

### Step 5 — On notification, read the response

When the harness notifies you that the background bash has completed, read
`/tmp/planforge-{run_id}/response.json`.

### Step 6 — Process the response

If `action == "approve"`:
  - Save the final HTML to a per-run converged.html in the case study directory.
  - Inform the user: "Plan approved. Final converged HTML saved to <path>."
  - Exit the loop.

If `action == "iterate"`:
  - Read `delta` and `full_state` from the response.
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

- **Server timeout (30 min idle).** If the response.json contains `{"error": "timeout"}`,
  inform the user and ask whether to resume.
- **Browser open failed.** The bash open command may fail in headless / SSH sessions.
  Always print the URL to chat as a fallback.
- **Multiple concurrent skills.** Each invocation should generate a fresh run_id; tmp
  paths and PID files are isolated per run.
```

**Commit:**

```
feat(iterate-plan): add SKILL.md orchestration instructions for agent

Imperative-voice instructions covering the 6-step iteration workflow,
logging conventions, and failure modes. Skill now drives the closed
loop end-to-end with the server (3a) and HTML (3b) it ships with.

Refs: plan.md Phase 3c.
```

### Phase 3d — README, CHANGELOG, release

1. Update `README.md` with real install + usage instructions.
2. Run `git cliff -o CHANGELOG.md --tag v0.1.0` to regenerate the changelog from commits.
3. Optionally have the agent add a 1–2 line narrative summary at the top of the new version section.
4. Commit:
   ```
   docs: release v0.1.0 — first functional loop

   Updates README install/usage with concrete instructions.
   Regenerates CHANGELOG.md via git-cliff covering Phase 2 and Phase 3 commits.

   v0.1.0 is the first usable release: agent can drive the iterate-plan loop
   end-to-end with HTML editing in browser and structured delta round-trip.

   Refs: plan.md Phase 3d.
   ```
5. Tag and push:
   ```
   git tag -a v0.1.0 -m "v0.1.0 — first functional loop"
   git push origin main --tags
   ```
6. Optionally create a GitHub release via `gh release create v0.1.0` for visibility.

**Effort for Phase 3 total:** 5–6 days of focused work for one person.

**Decision points:**
- **Mermaid version:** v10 LTS (recommended) vs v11 latest. Lock in early to avoid breaking template across iterations.
- **Mermaid loading:** CDN (`unpkg.com/mermaid@10/dist/mermaid.min.js`) for v0.1.0, vendor locally for v0.2.0+.
- **Editor for text:** plain `<textarea>` for v0.1.0. CodeMirror 6 for prose editing is v0.2.0+.
- **Visual diagram editing:** out of scope for v0.1.0 (DSL-only edit + live render). Drag-drop topology is a v1.0+ research thread.
- **Layer A delta computation strategy:** simple per-section / per-diagram diff via JSON. More sophisticated semantic alignment for diagrams (stable IDs across edits) is a v0.2.0 refinement.

---

## Phase 4 — Thesis-research integration (upstream)

This phase **does not happen in this repo.** It is done in the [thesis-research](https://github.com/emiperez95/thesis-research) repo (where this plugin was conceived) by a separate agent or session. Listed here for context only.

In thesis-research:

1. New entry in `docs/methodology-log.md` recording the split-into-separate-repo decision and pinning planforge v0.1.0 SHA.
2. Schema update in `case-studies/url-shortener/experiments/README.md` adding fields `plugin_repo`, `plugin_version`, `plugin_commit` to the `context.md` template.
3. Reference to planforge in `case-studies/url-shortener/README.md` under "Tools used".
4. Possible update to `CLAUDE.md` "Working conventions" noting tooling lives in a separate repo.

**The planforge agent should NOT modify thesis-research.** Cross-repo work is coordinated manually or by the user.

---

## Phase 5 — First empirical run (upstream)

Also **not in this repo.** First real experiment using planforge runs in thesis-research, in the URL-shortener case study, producing the first empirical data for the upstream thesis.

Listed here only so the planforge agent knows that `v0.1.0` will be exercised on a real case study and bugs surfaced there should be ported back as `fix:` commits in planforge.

---

## Upstream context

planforge was carved out of [thesis-research](https://github.com/emiperez95/thesis-research) for distribution and citability. The upstream thesis investigates diagrams-as-spec for AI coding workflows; planforge is one of the empirical tools it produces.

If you are an agent picking up this plan, you do not need to read the thesis to build the plugin — `plan.md` and `SKILL.md` are self-contained. But if questions arise about *why* design decisions were made (e.g., "why Layer A semantic delta and not full HTML diff?"), the thesis repo's `docs/research/diagram-diff-representation.md` and `docs/methodology-log.md` are the canonical references.

## Open questions to resolve during execution

These are not blockers but should be revisited as Phase 3 lands:

1. **Run logging path resolution.** SKILL.md proposes `experiments/{run}/iterations/` but how does the skill know `{run}`? Either (a) the user/caller passes it as an arg in the seed prompt, or (b) the skill defaults to `/tmp/planforge-{run_id}/iterations/` and the user copies after. Decide before v0.1.0.
2. **Browser opening on non-macOS.** The `open` command varies. Should `server.py` print a platform-specific suggested command, or should the skill detect platform? Probably the skill, since it shells out.
3. **Multiple concurrent runs.** Per-run UUID handles isolation, but is there a UI / list-runs command? Defer to v0.2.0 unless v0.1.0 testing reveals friction.
4. **Browser auto-refresh on next turn.** When the agent generates a new plan after iteration, how does the user know to refresh? Either (a) the skill instructs the user to refresh, (b) the server pushes via SSE / WebSocket (more work), or (c) the new server URL differs each turn so the browser navigates fresh. Decide before v0.1.0.
5. **Mermaid syntax error handling.** If the user types invalid Mermaid in a diagram block, the live render fails. Should the Send button be disabled, or should the bad block be sent anyway with a warning?

## Effort summary

| Phase | Effort | Cumulative |
|---|---|---|
| 1 — Scaffold | 20 min | 20 min (done) |
| 2 — Skill skeleton + tag v0.0.0 | 10 min | 30 min |
| 3a — server.py | ~1 day | ~1.5 days |
| 3b — HTML + JS + CSS | ~1.5 days | ~3 days |
| 3c — SKILL.md content | ~0.5 day | ~3.5 days |
| 3d — README + CHANGELOG + release v0.1.0 | ~0.5 day | ~4 days |
| 4 — Thesis-research integration (upstream) | ~45 min | — |
| 5 — First empirical run (upstream) | ~2–3 hours | — |

Realistic single-developer timeline: **~1 week to v0.1.0** with focused effort, longer with interruptions.
