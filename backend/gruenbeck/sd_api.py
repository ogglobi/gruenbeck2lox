"""Grünbeck SD-series local XML API client.

The SD series communicates via an HTTP/XML interface at ``/mux_http``.
This module provides a basic implementation; adapt the request parameters
and XML paths to match your specific SD firmware version.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .client import GruenbeckClient
from .models import DeviceInfo, DeviceValues
from .parser import parse_sd_xml

logger = logging.getLogger(__name__)

# Parameter IDs to request from the SD device.
# Adjust this list based on your device's parameter table.
_SD_PARAM_IDS = [
    "D_A_1_Manuell",   # current flow
    "D_A_1_Rest",      # residual capacity
    "D_A_1_Ges",       # total capacity
    "D_Y_1_SalzR",     # salt range (days)
    "D_Y_1_SalzM",     # salt quantity (kg)
    "D_A_1_Haerte_E",  # input hardness
    "D_A_1_Haerte_A",  # output hardness
    "D_D_1_Reg",       # regeneration status
    "D_A_1_LetzteReg", # last regeneration
    "D_D_1_Fehler",    # error code
]


class SDApi(GruenbeckClient):
    """Client for Grünbeck SD-series devices.

    Communicates via the local XML interface at ``POST /mux_http``.
    No cloud connection is used.
    """

    async def get_realtime(self) -> DeviceValues:
        """Fetch real-time values from the SD device's local XML interface."""
        if self._http is None:
            raise RuntimeError("Client not open – use 'async with SDApi(...)'")

        try:
            resp = await self._http.get("/mux_http")
            resp.raise_for_status()
            return parse_sd_xml(resp.text)
        except (httpx.HTTPError, ET.ParseError) as exc:
            logger.error("SD device %s GET /mux_http failed: %s", self.host, exc)
            return DeviceValues()

    async def get_info(self) -> DeviceInfo:
        """Fetch static device information from the SD device."""
        if self._http is None:
            raise RuntimeError("Client not open – use 'async with SDApi(...)'")

        try:
            resp = await self._http.get("/mux_http")
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            return DeviceInfo(
                serial_number=_xml_text(root, ".//SerialNumber"),
                model=_xml_text(root, ".//Type"),
                firmware_version=_xml_text(root, ".//FirmwareVersion"),
            )
        except (httpx.HTTPError, ET.ParseError) as exc:
            logger.error("SD device %s info failed: %s", self.host, exc)
            return DeviceInfo()


def _xml_text(root: ET.Element, path: str) -> str | None:
    el = root.find(path)
    return el.text.strip() if el is not None and el.text else None
