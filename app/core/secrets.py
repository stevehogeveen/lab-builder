from __future__ import annotations

import os
from typing import Any


def resolve_secret(config_value: str = "", *, env_name: str = "") -> str:
    if str(config_value or ""):
        return str(config_value)
    if env_name:
        return os.getenv(env_name, "")
    return ""


def secret_present(config_value: str = "", *, env_name: str = "") -> bool:
    return bool(resolve_secret(config_value, env_name=env_name))


def redact_secret_map(values: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if any(token in str(key).lower() for token in ("password", "secret", "community", "passphrase")) and str(value or ""):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
