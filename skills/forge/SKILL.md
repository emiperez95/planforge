---
name: planforge
description: Use when the user wants to collaboratively draft or refine a software plan that would benefit from diagrams and iterative editing before being locked in. Skip for plain-text outlines, simple lists, or one-shot planning where a single written response is enough.
disable-model-invocation: false
---

# planforge — instructions

You are running a closed-loop human-AI coplanner. You generate an HTML plan
(prose sections + Mermaid diagrams), the user edits it in their browser and
clicks **Send**, and you pick up their edits and refine — looping until they
click **Approve & finalize**.

The synchronization primitive is a **background Bash process**: you start the
local server with `run_in_background=true` and end your turn. When the user
submits in the browser, the server writes a response file and exits; the
background task completes and the harness notifies you. You never poll.

## Run state — track these for the whole session

- **`run_id`** — a UUID v4 you generate once at turn 1 and reuse every turn.
- **`port`** — the port the turn-1 server binds. You reuse it every later turn
  so the user's browser tab can auto-advance. Capture it at turn 1 (below).
- **`turn`** — integer, starts at 1, increments each iterate.
- **`task_id`** — the background task id of the current server, so you can match
  its completion notification.

All run files live under `/tmp/planforge-{run_id}/` (create it if needed).

## Skill assets — where this skill's files live

This skill ships two files alongside this `SKILL.md`:

- `scripts/server.py` — the local HTTP server
- `assets/template.html` — the plan-page template

Resolve both **relative to this skill's base directory** — the absolute path your
context shows as `Base directory for this skill: …` when the skill loads. Below,
`{skill_dir}` denotes that directory. (This resolves correctly whether planforge
runs as a personal skill or as an installed plugin, with no `${CLAUDE_PLUGIN_ROOT}`
dependency.)

## Per-iteration workflow

### Step 1 — Generate the plan HTML

Read `{skill_dir}/assets/template.html`. Replace **only**
the JSON inside the `<script id="initial-state" type="application/json"> … </script>`
block with this turn's state; leave the rest of the file unchanged:

```json
{
  "meta":     { "title": "<plan title>", "turn": <N>, "run_id": "<run_id>" },
  "text":     [ { "id": "<slug>", "label": "<heading>", "value": "<prose>" } ],
  "diagrams": [ { "id": "<slug>", "label": "<heading>", "value": "<mermaid DSL>" } ]
}
```

- **Turn 1:** build the state from the user's seed prompt.
- **Turn N>1:** build from the previous `full_state` plus the `user_notes` and
  edits in the last `response.json`. Keep `id`s stable across turns so the user's
  edits map cleanly. `turn` must equal your tracked turn number — the browser
  uses it to detect the new version.

Write the result to `/tmp/planforge-{run_id}/plan.html`.

### Step 2 — Spawn the server (background)

**Turn 1** — auto-select a port and open the browser. Use Bash with
`run_in_background=true`:

```bash
python3 {skill_dir}/scripts/server.py \
    --plan     /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --run-id   {run_id} \
    --port     0
```

Note the returned **background task id** (that's `task_id`). Then **Read the
task's output file once** — the server prints a line like
`planforge: serving plan on http://127.0.0.1:54321/`. Extract the port and
remember it as `port`. (If the line isn't there yet, read once more — it appears
within a moment. This single read is not polling.) Show the URL to the user as a
fallback in case auto-open failed.

**Turn N>1** — reuse the port and do **not** open a new tab (the existing tab is
polling and will reload itself). Add `--no-browser` and the saved port:

```bash
python3 {skill_dir}/scripts/server.py \
    --plan     /tmp/planforge-{run_id}/plan.html \
    --response /tmp/planforge-{run_id}/response.json \
    --run-id   {run_id} \
    --port     {port} \
    --no-browser
```

### Step 3 — End your turn

Tell the user briefly and stop:

- Turn 1: "Plan opened in your browser (http://127.0.0.1:{port}/). Edit it and
  click **Send** when ready — you can keep chatting with me here in parallel,
  and I'll pick up your edits as soon as you send."
- Turn N: "Updated plan sent — your browser tab should refresh automatically.
  Edit and **Send** again, or **Approve & finalize** when it's ready."

Then **end the turn**. Do not poll, do not re-read the output to check progress.
If the user chats with you while editing, answer normally. You will be resumed by
a `<task-notification>` when the server's background task completes.

### Step 4 — On notification, read the response

When the `<task-notification>` for this run's `task_id` reports completed, read
`/tmp/planforge-{run_id}/response.json`. Its shape:

```json
{ "run_id": "...", "turn": <N>, "action": "iterate" | "approve",
  "full_state": { "text": {"<id>": "..."}, "diagrams": {"<id>": "..."} },
  "user_notes": "...", "timestamp": "..." }
```

### Step 5 — Process the response

**`action == "iterate"`:**
- Apply the user's `full_state` edits and treat `user_notes` as extra guidance.
- Increment `turn`. Loop back to Step 1 (Step 2 uses the Turn N>1 form).

**`action == "approve"`:**
- Regenerate the page from the approved `full_state` and save it to
  `/tmp/planforge-{run_id}/converged.html`.
- Tell the user: "Plan approved. Final plan saved to
  `/tmp/planforge-{run_id}/converged.html`." and give a short summary of the
  final plan in chat.
- Exit the loop.

## Logging

After every turn (including the approve), save under
`/tmp/planforge-{run_id}/iterations/`:

- `turn-{NN}_plan.html` — the HTML you served this turn.
- `turn-{NN}_response.json` — the user's response.

(`{NN}` zero-padded, e.g. `turn-01_plan.html`.) v0.1.0 keeps everything in
`/tmp/`; copy artifacts out yourself if you want to keep them. A durable,
configurable output location is planned for a later version.

## Failure modes

- **Browser didn't open** (headless / SSH): the server logs the URL to its
  output instead. Surface that URL so the user can open it manually.
- **Server error:** if `/tmp/planforge-{run_id}/server.err` exists, read it and
  show the user the traceback.
- **User closed the tab without sending:** the server keeps running and no
  notification fires (v0.1.0 has no idle timeout). If the user asks to stop,
  kill the background task (`task_id`).
- **Port busy on a later turn:** rare — `server.err` will show a bind failure.
  The server already retries briefly; if it still fails, the run's port is taken;
  start a fresh run (new `run_id`, `--port 0`).
