from __future__ import annotations

import base64
import json
from typing import Optional


def mask_token(token: str) -> str:
    if len(token) <= 16:
        return "***"
    return "%s...%s" % (token[:8], token[-6:])


def jwt_expires_at(access_token: str) -> Optional[int]:
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None
