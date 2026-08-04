from __future__ import annotations


class RailWardenError(RuntimeError):
    """Base exception for expected RailWarden failures."""


class ConfigurationError(RailWardenError):
    """Project or provider configuration is invalid."""


class GitError(RailWardenError):
    """A Git operation failed or the repository is unsafe for the operation."""


class ValidationError(RailWardenError):
    """Validation failed."""


class ProviderError(RailWardenError):
    """Provider invocation or health failure."""


# Backward-compatible exception name for callers migrating from the old package.
LfgError = RailWardenError
