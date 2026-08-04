# Worktree lifecycle

RailWarden creates a named branch and isolated worktree for each work package under the configured worktree root. It refuses an unregistered usable worktree, rejects a dirty worktree for normal execution, and permits repair only through an explicit repair flow. A worker commits only owned paths. Integration merges from the task branch into the configured integration branch and records validation evidence.

Generated worktrees must remain ignored. Inspect them before deletion; use Git worktree commands rather than deleting unknown directories. The demo removes only worktrees it created after a successful acceptance run.
