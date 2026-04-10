"""Factory function for creating the appropriate Grünbeck client."""

from __future__ import annotations

from .client import GruenbeckClient
from .sc_api import SCApi
from .sd_api import SDApi


def make_client(device_type: str, host: str, port: int = 80) -> GruenbeckClient:
    """Return the correct API client for *device_type* ('sc' or 'sd')."""
    if device_type == "sc":
        return SCApi(host=host, port=port)
    if device_type == "sd":
        return SDApi(host=host, port=port)
    raise ValueError(f"Unknown device type: {device_type!r}. Expected 'sc' or 'sd'.")
