from __future__ import annotations


class LfgError(RuntimeError):
    """Base exception for expected LFG failures."""


class ConfigurationError(LfgError):
    """Project or provider configuration is invalid."""


class GitError(LfgError):
    """A Git operation failed or the repository is unsafe for the operation."""


class ValidationError(LfgError):
    """Validation failed."""


class ProviderError(LfgError):
    """Provider invocation or health failure."""
