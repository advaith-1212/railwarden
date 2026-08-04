# Acceptance testing

The deterministic acceptance scenario is the public, credential-free proof path:

```bash
git init -b main
git config user.name demo
git config user.email demo@example.invalid
warden init --demo
warden demo run
```

It initializes a fresh repository, records an approved two-package dependency DAG, launches scripted fake workers in isolated worktrees, creates isolated commits, intentionally fails validation, records failure/handoff evidence, retries, validates, passes real integration gates, integrates successfully, emits `.railwarden-runtime/reports/demo-acceptance.json`, and removes only the worktrees it generated. An unexpected condition exits nonzero. CI runs the same e2e test. The fake provider is never a production adapter.

For live providers, use a protected disposable repository, least-privilege credentials, manually scoped secrets, and a human observer. Live verification is optional and not evidence of universal provider support.
