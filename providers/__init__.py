"""Provider registry and URL-based provider detection."""

from __future__ import annotations

from urllib.parse import urlparse

from . import doctoreto, paziresh24
from .base import ProviderSpec, WatcherError

# Fallback for legacy configuration files that predate the provider field
# and for URLs that match no registered provider host.
DEFAULT_PROVIDER = "doctoreto"

REGISTRY: dict[str, ProviderSpec] = {
    doctoreto.PROVIDER.name: doctoreto.PROVIDER,
    paziresh24.PROVIDER.name: paziresh24.PROVIDER,
}


def get_provider(name: str) -> ProviderSpec:
    provider = REGISTRY.get(name)

    if provider is None:
        raise WatcherError(f"Unknown provider: {name!r}")

    return provider


def provider_for_url(url: str) -> ProviderSpec | None:
    """Match a doctor URL to a registered provider by network location."""
    netloc = urlparse(url.strip()).netloc.lower()

    for provider in REGISTRY.values():
        if netloc in provider.hosts:
            return provider

    return None
