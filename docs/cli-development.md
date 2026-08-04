# CLI development

Commands are assembled in `railwarden.cli.main`. Add a subcommand with explicit arguments, a small command handler, documented output, and an integer exit status (`0` success, `2` user/configuration error). Prefer JSON for machine-readable state views and concise human output otherwise. Interactive prompts must have a non-interactive path; commands must not silently invoke paid providers.

Add `--help` coverage and a disposable-repository smoke test for changed command behavior. Treat existing command names, machine-readable fields, and exit semantics as compatibility surfaces; document deprecations before removing them.
