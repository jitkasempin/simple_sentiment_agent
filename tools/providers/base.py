"""Base exceptions for sentiment providers."""


class ProviderError(Exception):
    """Raised when an external provider fails to fetch or parse data."""
    pass
