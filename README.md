# planforge

> Closed-loop human-AI coplanner for spec + diagram authoring. The agent generates an HTML plan with editable Mermaid diagrams, you edit and send, the agent picks up the changes and continues. Built for Claude Code.

## Status

✅ **v0.1.0 — first functional release.** The loop runs end-to-end. See [`plan.md`](./plan.md) for the roadmap and what's next.

## What it is

A [Claude Code](https://claude.com/claude-code) plugin that provides a `forge` skill (invoked as `/planforge:forge`). It runs a closed feedback loop between the agent and you:

1. The agent generates an HTML plan — prose sections + Mermaid diagrams — and writes it to disk.
2. The skill launches a local stdlib HTTP server in the background and opens the page in your browser.
3. You read, edit the prose, and edit the Mermaid diagrams (they re-render live). Meanwhile you can keep chatting with the agent in parallel.
4. You click **Send to agent** (or **Approve & finalize**). The browser POSTs the full edited plan state to the local server.
5. The server writes the response to disk and exits — the background task completing notifies the agent automatically (no polling).
6. The agent picks up your edits, refines the plan, and serves the next version (your tab auto-advances). The loop continues until you **Approve**.

No API keys to manage — the plugin reuses the agent's existing Claude Code session.

## Why

Most diagram-as-spec workflows force you into either pure-text iteration (no visual editing) or out-of-band tooling (Figma, then paste back into chat). planforge closes the loop: rendered diagrams you can edit, with automatic round-trip back to the agent.

Built originally to support empirical work on diagram-driven AI coding pipelines. It generalizes to any human-AI co-creation task whose artifact mixes structured parts (diagrams, code, JSON) with prose.

## Requirements

- [Claude Code](https://claude.com/claude-code)
- Python 3.10+ (the local server is stdlib-only — no pip installs)
- Internet access for the first render (Mermaid loads from a CDN; local vendoring is planned)

## Install

planforge isn't on a marketplace yet. For v0.1.0, load it from a local clone:

```bash
git clone https://github.com/emiperez95/planforge.git
claude --plugin-dir ./planforge
```

`--plugin-dir` loads the plugin for that Claude Code session. Marketplace distribution is planned for a later release.

## Usage

In a Claude Code session with the plugin loaded:

```
/planforge:forge plan a URL shortener service
```

The agent drafts a plan and opens it in your browser. Edit the prose and Mermaid diagrams, then:

- **Send to agent** — hand your edits back; the agent refines and serves the next turn (your tab auto-advances).
- **Approve & finalize** — end the loop; the agent saves the final `converged.html`.

Per-run artifacts (each turn's HTML + your responses, plus the final `converged.html`) are written under `/tmp/planforge-<run_id>/`.

## Development

See [`plan.md`](./plan.md) for the phased roadmap. Phase 2 (functional v0.1.0) is complete; remaining work and deferred features are tracked there.

### Dogfooding it globally while you develop

To use planforge across your other projects without a per-session `--plugin-dir`
or a copied marketplace install, symlink the skill into your personal skills
directory:

```bash
ln -s "$(pwd)/skills/forge" ~/.claude/skills/planforge
```

It then loads in every session as `/planforge`, and edits to the repo are live —
no reinstall. The skill resolves `server.py` / `template.html` relative to its own
base directory, so the same `SKILL.md` works both as this personal-skill symlink
and as a namespaced plugin (`/planforge:forge`). Skills load at session start, so
open a fresh session to pick it up.

Contributions follow [Conventional Commits](https://conventionalcommits.org/). Enable the commit-msg hook:

```bash
git config core.hooksPath .githooks
```

The CHANGELOG is regenerated from commit history via [git-cliff](https://git-cliff.org/) at each release.

## License

[MIT](./LICENSE)
