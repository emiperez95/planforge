# planforge

> Closed-loop human-AI coplanner for spec + diagram authoring. Browser-based iteration: agent generates an HTML plan with editable Mermaid diagrams, user edits and sends, agent picks up the changes and continues. Built for Claude Code.

## Status

🚧 **v0.0.0 — scaffold only.** Not yet functional. See [`plan.md`](./plan.md) for the development roadmap.

## What it is

A [Claude Code](https://claude.com/claude-code) plugin that provides a `iterate-plan` skill. The skill orchestrates a closed feedback loop between the agent and a human collaborator working on a software plan:

1. Agent generates an initial HTML artifact containing the plan (text sections + Mermaid diagrams)
2. The skill launches a local HTTP server (via `Bash run_in_background=true`) and opens the browser detached
3. The human reads, edits text, modifies diagrams, clicks "Send to agent"
4. The browser POSTs a structured delta to the local server
5. The server writes the response to disk and exits — Claude Code notifies the agent automatically
6. The agent picks up the changes, refines the plan, repeats — until the human clicks "Approve & finalize"

No API keys to manage (the plugin reuses the agent's existing Claude Code session). Distributable via the Claude Code plugin marketplace.

## Why

Most diagram-as-spec workflows force users into either: pure-text iteration (no visual editing) or out-of-band tooling (Figma + paste back into chat). planforge closes the loop: rendered diagrams that you can edit, with automatic round-trip back to the agent.

Built originally to support empirical work on diagram-driven AI coding pipelines. Generalizes to any human-AI co-creation task where the artifact has both structured (diagrams, code, JSON) and prose components.

## Install

*Not yet available — pending v0.1.0 release. Installation instructions will land here.*

## Usage

*Coming with v0.1.0.*

## Development

See [`plan.md`](./plan.md) for the phased development roadmap (phases 2–5). Phase 1 (scaffold) is complete.

Contributions follow [Conventional Commits](https://conventionalcommits.org/). Install the commit-msg hook:

```bash
git config core.hooksPath .githooks
```

CHANGELOG is generated via [git-cliff](https://git-cliff.org/) from commit history.

## License

[MIT](./LICENSE)
