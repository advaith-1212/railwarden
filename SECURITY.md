# Security Policy

This project is experimental. Please do not include secrets, private logs, or
provider transcripts in issues or pull requests.

## Reporting

Open a GitHub issue for non-sensitive security concerns. For sensitive reports,
contact the repository owner privately.

## Secret Handling

Tracked files must not contain raw provider credentials. LFG should store
environment references such as `env:OPENAI_API_KEY` in configuration, while
runtime-only secret material remains in ignored files such as
`.lfg-runtime/secrets.env` or `~/.lfg/launch-setups.d/<setup>.json`.

If a real credential has been committed, rotate it. Removing it from the latest
tree is not sufficient.
