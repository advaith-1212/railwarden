from __future__ import annotations

from lfg.providers.health import classify_failure


def test_quota_failure_classification() -> None:
    kind, transient, requires_human, pattern = classify_failure("quota exceeded")
    assert kind == "quota_exhausted"
    assert transient
    assert not requires_human
    assert pattern == "quota exceeded"


def test_git_permission_not_provider_outage() -> None:
    kind, transient, _requires_human, _pattern = classify_failure(
        "permission denied .git"
    )
    assert kind == "git_failure"
    assert not transient
