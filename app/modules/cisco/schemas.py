from __future__ import annotations

from pydantic import BaseModel


class CiscoModuleContext(BaseModel):
    module_name: str = "cisco"

