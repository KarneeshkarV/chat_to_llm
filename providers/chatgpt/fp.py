from __future__ import annotations

import logging
import uuid

import ua_generator
from ua_generator.data.version import VersionRange
from ua_generator.options import Options

logger = logging.getLogger(__name__)


def generate_fingerprint() -> dict:
    options = Options(
        version_ranges={
            "chrome": VersionRange(min_version=124),
            "edge": VersionRange(min_version=124),
        }
    )
    ua = ua_generator.generate(
        device=("desktop",),
        browser=("chrome", "edge"),
        platform=("windows", "macos"),
        options=options,
    )

    fp: dict = {
        "user-agent": ua.text,
        "impersonate": "chrome120",
        "oai-device-id": str(uuid.uuid4()),
    }

    if ua.device == "desktop" and ua.browser in ("chrome", "edge"):
        fp["sec-ch-ua-platform"] = ua.ch.platform
        fp["sec-ch-ua"] = ua.ch.brands
        fp["sec-ch-ua-mobile"] = ua.ch.mobile

    return fp
