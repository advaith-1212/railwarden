# Planning

LFG uses Claude Opus 4.6 Thinking through Antigravity (`agy`) as the planning
architect. The default project configuration is:

```yaml
planning:
  provider: antigravity
  model: Claude Opus 4.6 (Thinking)
  approval_required: true
```

The planner doctor verifies that `agy` exists, that `agy models` lists the
configured model, and reports the exact non-interactive invocation shape. LFG
does not silently substitute another planner.
