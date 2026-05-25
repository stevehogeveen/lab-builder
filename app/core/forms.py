from __future__ import annotations

from typing import Any


def preserve_secret(submitted: Any, existing: Any) -> str:
    value = str(submitted or "")
    if value:
        return value
    return str(existing or "")
