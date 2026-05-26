# Git hooks

This directory contains opt-in git hooks for planforge contributors.

## Install

From the repo root:

```bash
git config core.hooksPath .githooks
```

This is per-clone (not committed in `.git/config`). After running it, your local hooks point here. Run once per fresh clone.

## Hooks

### `commit-msg`

Enforces [Conventional Commits](https://conventionalcommits.org/) on the first line of every commit message.

**Allowed format:**

```
<type>(<optional-scope>): <subject>
```

Types: `feat` `fix` `docs` `refactor` `perf` `test` `build` `ci` `chore` `style` `revert`
Subject: 1–72 characters.
Body and footer: free-form (multi-line allowed after the first line).
Breaking changes: `feat!:` or `feat(scope)!:` or `BREAKING CHANGE:` footer.

**Skipped automatically:** merge commits, revert commits, fixup/squash commits.

If the message doesn't match, the commit is aborted with a clear error and examples.

## Why opt-in?

`core.hooksPath` is a per-clone setting, not committed in the repo. This avoids surprising contributors who clone the repo without reading docs. The hook is here in version control so it's reviewable and shareable, but you have to explicitly enable it.

If you have a meta-tool that bootstraps repos (e.g., `direnv`, `make setup`), wire `git config core.hooksPath .githooks` into it.
