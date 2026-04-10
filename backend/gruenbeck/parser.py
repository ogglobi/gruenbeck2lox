"""XML / JSON parsing utilities for Grünbeck device responses."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

from .models import DeviceValues

logger = logging.getLogger(__name__)

# Mapping from SD XML element tag names → DeviceValues field names.
# Adapt tags to your specific SD firmware version.
_SD_XML_MAP: dict[str, str] = {
    "CurrentFlow": "current_flow",
    "currentFlow": "current_flow",
    "ResidualCapacity": "residual_capacity",
    "residualCapacity": "residual_capacity",
    "TotalCapacity": "total_capacity",
    "totalCapacity": "total_capacity",
    "SaltRange": "salt_range",
    "salt_range": "salt_range",
    "SaltQuantity": "salt_quantity",
    "salt_quantity": "salt_quantity",
    "HardnessIn": "water_hardness_in",
    "HardnessOut": "water_hardness_out",
    "RegenerationStatus": "regeneration_status",
    "LastRegeneration": "last_regeneration",
    "ErrorCode": "error_code",
}


def parse_sd_xml(xml_text: str) -> DeviceValues:
    """Parse an SD-series XML response and return normalised DeviceValues.

    The XML structure varies by firmware version. This parser tries a
    best-effort approach: it iterates all elements and checks tag names
    against *_SD_XML_MAP*.
    """
    valid = set(DeviceValues.model_fields)
    kwargs: dict[str, Any] = {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("Failed to parse SD XML response: %s", exc)
        return DeviceValues()

    for element in root.iter():
        dst = _SD_XML_MAP.get(element.tag)
        if dst and dst in valid and dst not in kwargs and element.text:
            raw = element.text.strip().replace(",", ".")
            kwargs[dst] = _coerce(raw)

    return DeviceValues(**kwargs)


def _coerce(value: str) -> int | float | str:
    """Try to coerce a string to int, then float, then leave as str."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
