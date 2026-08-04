# Safe adoption and limitations

Start with the deterministic demo in a disposable repository. Before controlled use, begin from clean Git state, push backups, enable protected branches and required CI, restrict work-package paths, scope provider credentials, keep runtime/worktree directories ignored, inspect generated worktrees, and require human review. Use dry-run/demo flows before live providers. Never trust worker-reported success without RailWarden gates.

RailWarden is evolving infrastructure. Provider CLIs, credentials, operating systems, Git hooks, and repository policies vary. It does not claim critical-production safety, autonomous security review, or safety against a malicious worker with broader machine access.
