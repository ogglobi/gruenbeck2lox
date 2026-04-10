"""HTTP (and UDP) push logic for Loxone Miniserver Virtual Inputs.

Push URL format (HTTP Virtual HTTP Input):
    http://<user>:<password>@<host>:<port>/dev/sps/io/<input-name>/<value>

Passwords are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import httpx

from .models import PushResult

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1, 2, 4)  # seconds between attempts (exponential back-off)
_HTTP_TIMEOUT = 8.0


async def push_http(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    input_name: str,
    value: Any,
) -> PushResult:
    """Push *value* to a Loxone Virtual HTTP Input via GET request.

    Retries up to 3 times with exponential back-off.
    The password is never written to any log output.
    """
    url = f"http://{host}:{port}/dev/sps/io/{input_name}/{value}"
    auth = (user, password)

    last_error: str = "no attempts made"
    for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url, auth=auth)
                if resp.is_success:
                    logger.debug(
                        "Loxone push OK: %s → %s = %s (attempt %d)",
                        host, input_name, value, attempt,
                    )
                    return PushResult(success=True, status_code=resp.status_code)

                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "Loxone push %s: %s → %s = %s (attempt %d)",
                    last_error, host, input_name, value, attempt,
                )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            last_error = str(exc)
            logger.warning(
                "Loxone push error %s → %s (attempt %d): %s",
                host, input_name, attempt, exc,
            )

        if delay is not None:
            await asyncio.sleep(delay)

    logger.error(
        "Loxone push failed after %d attempts: %s → %s", len(_RETRY_DELAYS) + 1, host, input_name
    )
    return PushResult(success=False, error=last_error)


async def push_udp(
    *,
    host: str,
    port: int,
    input_name: str,
    value: Any,
) -> PushResult:
    """Push *value* to a Loxone Miniserver Virtual UDP Input.

    Sends a raw UTF-8 datagram: ``<input_name>/<value>\\n``
    No authentication is used for UDP (Loxone limitation).
    """
    message = f"{input_name}/{value}\n".encode()
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _udp_send, host, port, message)
        logger.debug("Loxone UDP push OK: %s → %s = %s", host, input_name, value)
        return PushResult(success=True)
    except OSError as exc:
        logger.error("Loxone UDP push failed %s → %s: %s", host, input_name, exc)
        return PushResult(success=False, error=str(exc))


def _udp_send(host: str, port: int, message: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3.0)
        sock.sendto(message, (host, port))


async def push_udp_packet(
    *,
    host: str,
    port: int,
    message: str,
) -> PushResult:
    """Send a pre-formatted multi-field UDP packet to a Loxone Miniserver.

    The message contains one ``key=value`` pair per line.  Loxone evaluates
    each "Virtueller UDP Eingang Befehl" with Befehlserkennung ``key=\\v``
    to extract the corresponding value from the datagram.
    """
    raw = message.encode("utf-8")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _udp_send, host, port, raw)
        logger.debug(
            "Loxone UDP packet OK: %s:%d (%d bytes, %d fields)",
            host, port, len(raw), message.count("\n"),
        )
        return PushResult(success=True)
    except OSError as exc:
        logger.error("Loxone UDP packet failed %s:%d: %s", host, port, exc)
        return PushResult(success=False, error=str(exc))
