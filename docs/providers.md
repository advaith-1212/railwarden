# Providers

LFG uses four explicit agent roles:

- Planning architect: Antigravity with `Claude Opus 4.6 (Thinking)`.
- Codex worker: `codex` with GPT-5.5 and high reasoning effort.
- Antigravity worker: `agy` with `Gemini 3.1 Pro (High)`.
- Composer worker: `grok` with Grok Composer 2.5.

Adapters expose health checks, command construction, cancellation, result
collection, and failure classification boundaries.
