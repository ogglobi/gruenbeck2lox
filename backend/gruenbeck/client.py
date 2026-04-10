"""Abstract base class for Grünbeck device API clients."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from .models import DeviceInfo, DeviceValues

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


class GruenbeckClient(ABC):
    """Abstract base for SC / SD Grünbeck device clients.

    Intended to be used as an async context manager::

        async with SCApi(host="192.168.1.50") as client:
            values = await client.get_realtime()
    """

    def __init__(self, host: str, port: int = 80) -> None:
        self.host = host
        self.port = port
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def __aenter__(self) -> "GruenbeckClient":
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @abstractmethod
    async def get_realtime(self) -> DeviceValues:
        """Fetch and return normalised real-time values from the device."""

    @abstractmethod
    async def get_info(self) -> DeviceInfo:
        """Fetch and return static device information."""

    async def test_connection(self) -> bool:
        """Return True if the device responds within 5 seconds."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/")
                return resp.status_code < 500
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            return False
