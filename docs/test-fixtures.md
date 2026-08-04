# Test fixtures

Fixtures must be deterministic, local, and credential-free. Use temporary Git repositories with a configured test identity, fake executables/process results, and explicitly named generated outputs. Never place real tokens in fixtures; use recognizably fake values and assert redaction. Acceptance fixtures must clean only paths they created and write a machine-readable report.
