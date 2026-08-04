# Security

RailWarden coordinates agents that can edit code, launch processes, and call provider
CLIs. Treat it as a local automation runtime with explicit trust boundaries.

## Tracked Versus Ignored

Tracked:

- `.railwarden/project.yaml`
- `.railwarden/work_packages.yaml`
- `.railwarden/validation.yaml`
- context files
- documentation
- source and tests

Ignored:

- `.env`
- `.railwarden-runtime/`
- `.railwarden-worktrees/`
- runtime logs
- provider transcripts
- generated process files
- runtime result JSON
- local setup secrets
- worktrees

## Secrets

Tracked files must not contain raw provider credentials. Store references such
as:

```text
env:OPENAI_API_KEY
env:ANTHROPIC_API_KEY
env:GEMINI_API_KEY
```

Runtime-only secret material may be written to ignored files such as:

```text
~/.railwarden/launch-setups.d/<setup>.json
.railwarden-runtime/secrets.env
```

These files should use restrictive permissions and should never be committed.

## Worker Sandboxing

Workers should be constrained by:

- assigned worktree
- owned paths
- forbidden paths
- validation commands
- result schema
- merge gates

RailWarden should verify changed files before accepting a worker result. A worker's
claim is not enough.

## Public Repository Checklist

Before publishing:

```bash
git status --short --branch
git ls-files
git ls-files | rg '(^|/)(\\.env|.*secret.*|.*token.*|auth\\.json|credentials)'
rg -n '(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|github_pat_|AKIA[0-9A-Z]{16}|BEGIN .*PRIVATE KEY)'
```

If any real credential has ever been committed, rotate it. Removing it from the
latest tree is not enough.
